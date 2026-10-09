"""The trainer: a separate program that carries out the app's training runs.

It asks the app for the next waiting run, runs that run's model recipe as a process of its own, and
passes on what the recipe reports. It only talks to the app over HTTP (with a secret the two share)
and only touches the training folder they both see -- never the database or the slides.

    python trainer/trainer.py

Environment:
    APP_URL        where the app answers            (default http://127.0.0.1:8088)
    TRAINING_DIR   the folder shared with the app   (default <repository>/data/training)
    RECIPES_DIR    the model recipes                (default the recipes/ folder beside this file)
    ENVS_DIR       where recipes' extra packages go (default <TRAINING_DIR>/envs)

What a recipe is told and must answer is described in recipes/README.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import site
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:8088").rstrip("/")
TRAINING_DIR = Path(os.environ.get("TRAINING_DIR", HERE.parent / "data" / "training"))
RECIPES_DIR = Path(os.environ.get("RECIPES_DIR", HERE / "recipes"))
SECRET_FILE = TRAINING_DIR / ".trainer-secret"

IDLE_POLL_S = 3  # how often to ask for work
HEARTBEAT_S = 2  # how often a run in progress reports (and learns whether to stop)
METRIC_PREFIX = "VP_METRIC "  # a line of a recipe's output carrying one epoch's numbers as JSON
READY_LINE = "VP_READY"  # printed by a recipe's predict.py once its model is loaded
RESULT_PREFIX = "VP_RESULT "  # ...and before each answer
MODEL_LOAD_S = 150  # how long a model may take to load
ANSWER_S = 120  # ...and to answer one request
MODEL_IDLE_S = 600  # a loaded model nobody asks is let go after this long
CHECK_TRAIN_S = 900  # how long a recipe's check may train for
PACKAGES_S = 1800  # ...and how long installing a recipe's extra packages may take

# Set while a training run is in progress: it gets the graphics card to itself, and suggestions are
# made on the CPU meanwhile (slower, but they neither wait for hours nor crash the training).
TRAINING = threading.Event()


_COLOURS = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_BAR = re.compile(r"\d+%\s*[\u2500-\u259f|#]")  # a percentage followed by the bar's block characters


def for_the_log(line: str) -> str | None:
    """A line of a recipe's output as it should be kept: without terminal colour codes, and not at all
    if it is one of the many redraws of a progress bar (only its last, at 100%, is kept)."""
    line = _COLOURS.sub("", line)
    if _BAR.search(line) and "100%" not in line:
        return None
    return line


def say(text: str) -> None:
    print(f"[trainer] {text}", flush=True)


def hardware() -> dict:
    """The graphics cards this program can use; ``device`` is "cpu" when there are none."""
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 - no PyTorch at all is worth telling the app
        return {"device": "cpu", "gpus": [], "problem": f"PyTorch is not installed: {exc}"}
    gpus = []
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            gpus.append({"index": i, "name": props.name, "memory_mb": props.total_memory // (1024 * 1024)})
    return {"device": "cuda" if gpus else "cpu", "gpus": gpus, "torch": torch.__version__}


def call(path: str, payload: dict, timeout: float = 30) -> dict:
    request = urllib.request.Request(
        f"{APP_URL}/api/trainer{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Trainer-Secret": SECRET_FILE.read_text().strip()},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def recipe_folder(job: dict) -> Path:
    """Where a job's recipe is: one of your own in the shared folder, or one of the built-in ones here."""
    return TRAINING_DIR / job["recipe_folder"] if job.get("recipe_folder") else RECIPES_DIR / job["recipe_id"]


def remove(folder: Path) -> None:
    """Delete a folder, trying again for a moment: on Windows a program that has just ended can still hold its files."""
    for _ in range(10):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.5)


def copy_code(recipe: Path, target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(recipe, target, ignore=shutil.ignore_patterns("__pycache__", ".*"))


def python_for(code: Path) -> str:
    """The Python to run this code with: the trainer's own, or -- when the recipe lists extra packages in
    requirements.txt -- an environment that has them on top of the trainer's. Environments are made
    once, named after what they hold (so recipes wanting the same packages share one), and kept in
    <training folder>/envs. Raises RuntimeError with pip's own words when a package cannot be installed."""
    file = code / "requirements.txt"
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines() if file.is_file() else []
    wanted = sorted(line.strip() for line in lines if line.strip() and not line.strip().startswith("#"))
    if not wanted:
        return sys.executable
    env = Path(os.environ.get("ENVS_DIR") or TRAINING_DIR / "envs") / hashlib.sha256("\n".join(wanted).encode()).hexdigest()[:16]
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = env / ".ready"
    if ready.is_file() and ready.read_text(encoding="utf-8").strip() == sys.version and python.is_file():
        return str(python)

    say(f"installing packages: {', '.join(wanted)}")
    shutil.rmtree(env, ignore_errors=True)
    env.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(env)], check=True, capture_output=True, text=True, timeout=300)
        # When the trainer itself runs in a virtual environment, "system" packages are not its own: point
        # the new environment at the trainer's, so PyTorch and the rest are there without a second copy.
        target = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], check=True, capture_output=True, text=True, timeout=60).stdout.strip()
        Path(target, "_trainer_packages.pth").write_text("\n".join(p for p in site.getsitepackages() if Path(p).is_dir()) + "\n", encoding="utf-8")
        requirements = env / "requirements.txt"
        requirements.write_text("\n".join(wanted) + "\n", encoding="utf-8")
        done = subprocess.run([str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "-r", str(requirements)], capture_output=True, text=True, timeout=PACKAGES_S)
    except subprocess.TimeoutExpired:
        shutil.rmtree(env, ignore_errors=True)
        raise RuntimeError("Installing the recipe's packages took too long.") from None
    except (subprocess.CalledProcessError, OSError) as exc:
        shutil.rmtree(env, ignore_errors=True)
        raise RuntimeError(f"The environment for the recipe's packages could not be made: {getattr(exc, 'stderr', '') or exc}") from None
    if done.returncode != 0:
        shutil.rmtree(env, ignore_errors=True)
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-12:])
        raise RuntimeError(f"The recipe's packages could not be installed (requirements.txt):\n{tail}")
    ready.write_text(sys.version, encoding="utf-8")
    return str(python)


def make_check_dataset(folder: Path, task: str = "detection") -> None:
    """A few made-up images, laid out exactly like a real run's dataset for that task: coloured boxes
    to find or outline, or -- for classification -- images that are mostly one colour or the other."""
    import random

    from PIL import Image, ImageDraw

    rng = random.Random(7)
    classes = [{"id": 11, "name": "Red box"}, {"id": 12, "name": "Blue box"}]
    colours = {11: (200, 40, 40), 12: (40, 60, 200)}
    layout = {}
    if task == "classification":
        for name, count in (("train", 8), ("val", 4), ("test", 4)):
            rows = ["file,class"]
            for i in range(count):
                cls = classes[i % 2]
                (folder / name / "images" / cls["name"]).mkdir(parents=True, exist_ok=True)
                image = Image.new("RGB", (160, 160), (235, 225, 235))
                ImageDraw.Draw(image).rectangle([rng.randint(0, 40), rng.randint(0, 40), rng.randint(100, 160), rng.randint(100, 160)], fill=colours[cls["id"]])
                image.save(folder / name / "images" / cls["name"] / f"{name}_{i + 1}.jpg", quality=92)
                rows.append(f"images/{cls['name']}/{name}_{i + 1}.jpg,{cls['name']}")
            (folder / name / "labels.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            layout[name] = {"labels": f"{name}/labels.csv", "images": name}
        (folder / "dataset.json").write_text(json.dumps({"task": task, "format": "folders", "classes": classes, "sets": layout}, indent=2), encoding="utf-8")
        return
    for name, count in (("train", 6), ("val", 2), ("test", 2)):
        (folder / name / "images").mkdir(parents=True)
        (folder / name / "annotations").mkdir()
        images, annotations = [], []
        for i in range(count):
            image = Image.new("RGB", (320, 320), (235, 225, 235))
            draw = ImageDraw.Draw(image)
            for _ in range(rng.randint(1, 3)):
                cls = rng.choice(classes)["id"]
                w, h = rng.randint(30, 80), rng.randint(30, 80)
                x, y = rng.randint(0, 320 - w), rng.randint(0, 320 - h)
                draw.rectangle([x, y, x + w, y + h], fill=colours[cls])
                annotations.append({
                    "id": len(annotations) + 1, "image_id": i + 1, "category_id": cls, "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0,
                    "segmentation": [[x, y, x + w, y, x + w, y + h, x, y + h]],
                })
            file = f"{name}_{i + 1}.jpg"
            image.save(folder / name / "images" / file, quality=92)
            images.append({"id": i + 1, "file_name": file, "width": 320, "height": 320})
        doc = {"images": images, "annotations": annotations, "categories": classes}
        (folder / name / "annotations" / "dataset_coco.json").write_text(json.dumps(doc), encoding="utf-8")
        layout[name] = {"annotations": f"{name}/annotations/dataset_coco.json", "images": f"{name}/images"}
    (folder / "dataset.json").write_text(json.dumps({"task": task, "format": "coco", "classes": classes, "sets": layout}, indent=2), encoding="utf-8")


def run_check(job: dict, hw: dict) -> None:
    """Try one of your own recipes as it is now -- install its packages, train one epoch on made-up
    images, load the result and ask it about an image -- and tell the app how each step went."""
    say(f"checking recipe {job['id']}")
    work = TRAINING_DIR / "checks" / job["id"]
    shutil.rmtree(work, ignore_errors=True)
    code, output, dataset = work / "code", work / "output", work / "dataset"
    steps: list[dict] = []
    log: list[str] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    try:
        copy_code(TRAINING_DIR / job["folder"], code)
        output.mkdir(parents=True)
        try:
            python = python_for(code)
            if python != sys.executable:
                step("Install the packages in requirements.txt", True)
        except RuntimeError as exc:
            python = None
            step("Install the packages in requirements.txt", False, str(exc))

        trained = False
        if python and job["trainable"]:
            make_check_dataset(dataset, job.get("task", "detection"))
            (work / "settings.json").write_text(json.dumps(job["settings"]), encoding="utf-8")
            try:
                done = subprocess.run(
                    [python, "-u", str(code / "train.py"), "--dataset", str(dataset), "--output", str(output), "--settings", str(work / "settings.json")],
                    cwd=code, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=CHECK_TRAIN_S,
                )
                lines = (done.stdout + done.stderr).splitlines()
                log = [line for line in lines if not line.startswith(METRIC_PREFIX)]
                if step("Run train.py for one epoch", done.returncode == 0, "" if done.returncode == 0 else f"It ended with code {done.returncode}; see the log below."):
                    metrics = []
                    for line in lines:
                        if line.startswith(METRIC_PREFIX):
                            try:
                                metrics.append(json.loads(line[len(METRIC_PREFIX):]))
                            except ValueError:
                                metrics.append(None)
                    good = bool(metrics) and all(isinstance(m, dict) and "epoch" in m and "epochs" in m for m in metrics)
                    step("Report each epoch (a VP_METRIC line with \"epoch\" and \"epochs\")", good, "" if good else ("No VP_METRIC line was printed." if not metrics else "A VP_METRIC line was not JSON with \"epoch\" and \"epochs\"."))
                    wrote = step("Write the trained model (model.pt)", (output / "model.pt").is_file())
                    try:
                        result_ok = isinstance(json.loads((output / "result.json").read_text(encoding="utf-8")), dict)
                    except (OSError, ValueError):
                        result_ok = False
                    step("Write the final scores (result.json)", result_ok, "" if result_ok else "result.json is missing or not a JSON object.")
                    trained = wrote
            except subprocess.TimeoutExpired:
                step("Run train.py for one epoch", False, f"It was still running after {CHECK_TRAIN_S // 60} minutes.")

        if trained and job["has_predict"]:
            loaded = None
            try:
                loaded = Predictor({"model_id": "check", "code": str(code), "weights": str(output / "model.pt")}, hw["device"], python)
                step("Load the model in predict.py", True)
                image = next(p for p in (dataset / "val" / "images").rglob("*.jpg"))
                answer = loaded.ask({"id": 1, "images": [str(image)], "min_score": 0.05})
                good = (
                    isinstance(answer, list) and len(answer) == 1 and isinstance(answer[0], list)
                    and all(
                        isinstance(d, dict) and "score" in d and ("class_id" in d or "class" in d)
                        and len(d.get("box", [0] * 4)) == 4 and len(d.get("polygon", [0] * 3)) >= 3
                        for d in answer[0]
                    )
                )
                step("Suggest on one image", good, f"{len(answer[0])} objects found (a model trained this little need not find any)." if good else "The answer is not, per image, a list of {score, class_id, and a box or a polygon for a shape}.")
            except RuntimeError as exc:
                step("Load the model in predict.py" if not any(s["name"].startswith("Load") for s in steps) else "Suggest on one image", False, str(exc))
            finally:
                if loaded:
                    loaded.close()
        if not steps:
            step("Read the recipe", True, "It has no training code to try; a model file is tried when it is added to a project.")
    except Exception as exc:  # noqa: BLE001 - the app must hear how the check ended, whatever happened
        step("Check the recipe", False, f"The trainer could not carry out the check: {exc}")
    finally:
        remove(work)

    ok = all(s["ok"] for s in steps)
    say(f"recipe {job['id']}: {'passed' if ok else 'failed'}")
    call(f"/checks/{job['id']}/result", {"hash": job["hash"], "ok": ok, "report": {"steps": steps, "log": "\n".join(log[-60:])}})


def run_recipe(job: dict, hw: dict) -> None:
    run_id = job["id"]
    folder = TRAINING_DIR / job["folder"]
    output = folder / "output"
    output.mkdir(parents=True, exist_ok=True)
    # The run keeps the code exactly as it was run, whatever happens to the recipe later.
    code = folder / "code"
    copy_code(recipe_folder(job), code)
    try:
        python = python_for(code)
    except RuntimeError as exc:  # a package could not be installed: say so in the run's own log
        (output / "train.log").write_text(f"{exc}\n", encoding="utf-8")
        call(f"/runs/{run_id}/finish", {"status": "failed", "error": str(exc)})
        return
    settings_file = folder / "settings.json"
    settings_file.write_text(json.dumps(job["settings"], indent=2), encoding="utf-8")

    say(f"run {run_id}: {job['recipe_id']} {job['settings']}")
    process = subprocess.Popen(
        [python, "-u", str(code / "train.py"), "--dataset", str(folder / "dataset"), "--output", str(output), "--settings", str(settings_file)],
        cwd=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )

    pending: list[dict] = []  # epochs reported by the recipe, not yet passed on
    lock = threading.Lock()

    def read_output() -> None:
        with (output / "train.log").open("a", encoding="utf-8") as log:
            for line in process.stdout:
                if line.startswith(METRIC_PREFIX):
                    try:
                        with lock:
                            pending.append(json.loads(line[len(METRIC_PREFIX):]))
                    except ValueError:
                        pass
                    continue
                kept = for_the_log(line)
                if kept is not None:
                    log.write(kept)
                    log.flush()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    device = hw["gpus"][0]["name"] if hw["gpus"] else "CPU"
    stopped = False

    def report() -> bool:
        """Pass on what has been reported so far; True when the app wants the run stopped."""
        with lock:
            batch, pending[:] = list(pending), []
        stop = False
        for metrics in batch or [None]:
            try:
                stop = call(f"/runs/{run_id}/progress", {"metrics": metrics, "device": device}).get("stop", False) or stop
            except (urllib.error.URLError, OSError) as exc:  # the app is restarting: keep training, tell it later
                say(f"could not report progress ({exc}); will try again")
                if metrics is not None:
                    with lock:
                        pending.insert(0, metrics)
                break
        return stop

    while process.poll() is None:
        if report() and not stopped:
            say(f"run {run_id}: stopping")
            stopped = True
            process.terminate()
        time.sleep(HEARTBEAT_S)
    reader.join(timeout=10)
    report()

    result_file = output / "result.json"
    if stopped:
        outcome = {"status": "stopped"}
    elif process.returncode == 0 and result_file.is_file() and (output / "model.pt").is_file():
        outcome = {"status": "done", "result": json.loads(result_file.read_text(encoding="utf-8"))}
    else:
        lines = (output / "train.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
        reason = "\n".join(lines[-15:]) or f"The recipe ended with code {process.returncode} and wrote no model."
        outcome = {"status": "failed", "error": reason}
    say(f"run {run_id}: {outcome['status']}")
    for _ in range(30):  # the result must reach the app, even if it is restarting right now
        try:
            call(f"/runs/{run_id}/finish", outcome)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(5)


class Predictor:
    """A model kept loaded: its recipe's predict.py as a process that answers one request per line."""

    def __init__(self, job: dict, device: str, python: str | None = None):
        self.model_id, self.device, self.used = job["model_id"], device, time.time()
        code = TRAINING_DIR / job["code"]
        if not (code / "predict.py").is_file():
            raise RuntimeError("This model's recipe has no prediction code (predict.py).")
        self.process = subprocess.Popen(
            [python or python_for(code), "-u", str(code / "predict.py"), "--model", str(TRAINING_DIR / job["weights"]), "--device", device],
            cwd=code, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.said: list[str] = []  # what it printed besides answers, for the error message if it dies
        threading.Thread(target=self._read, daemon=True).start()
        ready = self._await(lambda line: line.startswith(READY_LINE), MODEL_LOAD_S, "load")
        try:  # what the model says about itself (its class names), if anything
            self.info = json.loads(ready[len(READY_LINE):]) if ready[len(READY_LINE):].strip() else {}
        except ValueError:
            self.info = {}

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def _await(self, wanted, timeout: float, doing: str) -> str:
        deadline = time.time() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                self.close()
                raise RuntimeError(f"The model took more than {timeout:.0f} s to {doing}.") from None
            if line is None:
                raise RuntimeError("The model's prediction code stopped: " + (" | ".join(self.said[-6:]) or "it printed nothing"))
            if wanted(line):
                return line
            self.said.append(line)

    def ask(self, job: dict) -> list:
        self.used = time.time()
        request = {"id": job["id"], "images": [str(TRAINING_DIR / image) for image in job["images"]], "min_score": job.get("min_score", 0.05)}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        answer = json.loads(self._await(lambda line: line.startswith(RESULT_PREFIX), ANSWER_S, "answer")[len(RESULT_PREFIX):])
        return answer["results"]

    def close(self) -> None:
        self.process.kill()
        try:  # until it has really gone, its folder cannot be removed (Windows)
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def predict_loop(hw: dict) -> None:
    """Answer the workspace's "what do you see in this patch?" requests, keeping the last model loaded."""
    loaded: Predictor | None = None
    while True:
        try:
            job = call("/predictions/claim", {}, timeout=60).get("job") if SECRET_FILE.is_file() else None
        except (urllib.error.URLError, OSError):
            job = None
            time.sleep(IDLE_POLL_S)
        device = "cpu" if TRAINING.is_set() else hw["device"]
        if loaded and (loaded.device != device or time.time() - loaded.used > MODEL_IDLE_S or (job and loaded.model_id != job["model_id"])):
            loaded.close()  # the card is needed for training, or nobody is asking, or another model is wanted
            loaded = None
        if job is None:
            if not SECRET_FILE.is_file():
                time.sleep(IDLE_POLL_S)
            continue
        outcome: dict
        try:
            if loaded is None:
                say(f"loading model {job['model_id']} on {device}")
                loaded = Predictor(job, device)
            outcome = {"results": loaded.ask(job) if job["images"] else [], "info": loaded.info}
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, the person waiting must be told
            say(f"prediction {job['id']} failed: {exc}")
            outcome = {"error": str(exc)}
            if loaded:
                loaded.close()
            loaded = None
        try:
            call(f"/predictions/{job['id']}/result", outcome)
        except (urllib.error.URLError, OSError):
            pass


def main() -> None:
    hw = hardware()
    say(f"app: {APP_URL}   training folder: {TRAINING_DIR}")
    say("hardware: " + (", ".join(f"{g['name']} ({g['memory_mb']} MB)" for g in hw["gpus"]) or f"no GPU -- training on CPU will be slow. {hw.get('problem', '')}"))
    threading.Thread(target=predict_loop, args=(hw,), daemon=True).start()
    fresh = True
    waiting_for_secret = False
    while True:
        try:
            if not SECRET_FILE.is_file():
                if not waiting_for_secret:
                    say(f"waiting for the app to create {SECRET_FILE} (it does so when it starts)")
                    waiting_for_secret = True
                time.sleep(IDLE_POLL_S)
                continue
            if fresh:
                call("/hello", {"hardware": hw, "fresh": True})
                fresh = False
                say("connected to the app")
            # Cutting a run's dataset happens inside this call, so it may take a long time to answer.
            work = call("/claim", {"hardware": hw}, timeout=6 * 3600)
            job, check = work.get("run"), work.get("check")
        except (urllib.error.URLError, OSError) as exc:
            if not fresh:
                say(f"the app is not answering ({exc}); retrying")
            fresh = True
            time.sleep(IDLE_POLL_S)
            continue
        if job is None and check is None:
            time.sleep(IDLE_POLL_S)
            continue
        TRAINING.set()
        if job is None:
            try:
                run_check(check, hw)
            except (urllib.error.URLError, OSError) as exc:
                say(f"the check's result could not be sent: {exc}")
            finally:
                TRAINING.clear()
            continue
        try:
            run_recipe(job, hw)
        except Exception as exc:  # noqa: BLE001 - one run going wrong must not stop the trainer
            say(f"run {job['id']} could not be carried out: {exc}")
            try:
                call(f"/runs/{job['id']}/finish", {"status": "failed", "error": f"The trainer could not run the recipe: {exc}"})
            except (urllib.error.URLError, OSError):
                pass
        finally:
            TRAINING.clear()


if __name__ == "__main__":
    main()
