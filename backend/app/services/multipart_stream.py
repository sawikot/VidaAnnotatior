"""Receives a multipart/form-data request body straight to disk.

Starlette's ``request.form()`` pushes every 64 KB chunk of an uploaded file through
a worker thread into a spooled temp file, and only hands the result over once the
whole body has arrived. For a 600 MB slide that is ~30 s of overhead (20 MB/s, on a
disk that copies the same file in 1.5 s), and a size limit can only be enforced
after every byte has already been stored. Here each part is written to its own
file as its bytes arrive, and the size limit stops the request the moment it is
crossed.

Uploaded bytes are stored under neutral names (``0.part``, ``1.part`` ...) --
the client-supplied file name is returned to the caller as data to validate, and is
never used to build a path here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException, Request

try:  # python-multipart >= 0.0.13 renamed its module
    from python_multipart.exceptions import MultipartParseError
    from python_multipart.multipart import MultipartParser, parse_options_header
except ImportError:  # pragma: no cover - depends on the installed version
    from multipart.exceptions import MultipartParseError
    from multipart.multipart import MultipartParser, parse_options_header

MAX_FIELD_BYTES = 16 * 1024 * 1024  # a manifest for 20,000 files is a few MB; anything near this is abuse
_WRITE_BUFFER = 1024 * 1024


@dataclass
class ReceivedFile:
    filename: str  # exactly as the client sent it -- untrusted
    path: Path  # where the bytes were stored


@dataclass
class ReceivedForm:
    files: list[ReceivedFile] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class _Receiver:
    def __init__(self, directory: Path, max_files: int, max_fields: int, max_bytes: int):
        self.directory = directory
        self.max_files, self.max_fields, self.max_bytes = max_files, max_fields, max_bytes
        self.form = ReceivedForm()
        self.file_bytes = 0
        self._fields_seen = 0
        self._header_name = b""
        self._header_value = b""
        self._disposition = b""
        self._out = None  # the open file of the part being received, if it is a file
        self._field_name: str | None = None  # the name of the part being received, if it is a plain field
        self._field_data = bytearray()

    # -- parser callbacks ---------------------------------------------------------------
    def on_part_begin(self) -> None:
        self._disposition = b""
        self._out = None
        self._field_name = None
        self._field_data = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = b""
        self._header_value = b""

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        if b"name" not in options:
            raise HTTPException(status_code=400, detail='The Content-Disposition header field "name" must be provided.')
        name = _decode(options[b"name"])
        if b"filename" in options:
            if len(self.form.files) >= self.max_files:
                raise HTTPException(status_code=400, detail=f"Too many files. Maximum number of files is {self.max_files}.")
            path = self.directory / f"{len(self.form.files)}.part"
            self._out = path.open("wb", buffering=_WRITE_BUFFER)
            self.form.files.append(ReceivedFile(filename=_decode(options[b"filename"]), path=path))
        else:
            self._fields_seen += 1
            if self._fields_seen > self.max_fields:
                raise HTTPException(status_code=400, detail=f"Too many fields. Maximum number of fields is {self.max_fields}.")
            self._field_name = name

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._out is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.max_bytes:
                raise HTTPException(status_code=413, detail="Upload is larger than the configured size limit")
            self._out.write(memoryview(data)[start:end])
        elif self._field_name is not None:
            self._field_data += data[start:end]
            if len(self._field_data) > MAX_FIELD_BYTES:
                raise HTTPException(status_code=413, detail="A form field is too large")

    def on_part_end(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None
        elif self._field_name is not None:
            self.form.fields[self._field_name] = _decode(bytes(self._field_data))
            self._field_name = None

    @property
    def part_unfinished(self) -> bool:
        """True if the body stopped in the middle of a part -- its bytes are a truncated file."""
        return self._out is not None or self._field_name is not None

    def close(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


async def receive_multipart(
    request: Request, directory: Path, *, max_files: int, max_bytes: int, max_fields: int = 20
) -> ReceivedForm:
    """Stream the request's multipart body into ``directory`` and return what arrived.

    A request that is not multipart/form-data yields an empty form. File bytes beyond
    ``max_bytes`` in total raise 413 immediately; a malformed body raises 400.
    """
    kind, params = parse_options_header(request.headers.get("content-type", ""))
    if kind.lower() != b"multipart/form-data":
        return ReceivedForm()
    boundary = params.get(b"boundary")
    if not boundary:
        raise HTTPException(status_code=400, detail="Missing boundary in multipart.")

    directory.mkdir(parents=True, exist_ok=True)
    receiver = _Receiver(directory, max_files, max_fields, max_bytes)
    parser = MultipartParser(boundary, receiver.callbacks())
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        if receiver.part_unfinished:
            raise HTTPException(status_code=400, detail="The upload ended before its last file was complete")
    except MultipartParseError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed multipart body: {exc}") from exc
    finally:
        receiver.close()
    return receiver.form
