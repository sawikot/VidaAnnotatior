from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin

# queued -> preparing (the dataset is being cut) -> running -> done | failed | stopped
ACTIVE_STATUSES = ("queued", "preparing", "running")
FINAL_STATUSES = ("done", "failed", "stopped")


class TrainingRun(Base, TimestampMixin):
    """One training of a model recipe on a project's annotations, carried out by the trainer
    (trainer/trainer.py). Its files -- dataset, the code as it was run, log, trained model -- live in
    ``<training dir>/runs/<id>``."""

    __tablename__ = "training_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)

    recipe_id: Mapped[str] = mapped_column(String(80))
    recipe_name: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(30))  # detection | classification | segmentation
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # the recipe's settings, as chosen

    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    # What it was trained on, fixed when the dataset is cut: slides and objects per set, the classes.
    dataset: Mapped[dict | None] = mapped_column(JSON, default=None)
    # One entry per finished epoch, as reported by the recipe (epoch, train_loss, val_... ).
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    epochs: Mapped[int] = mapped_column(Integer, default=0)
    # The recipe's final scores (validation and test), once done.
    result: Mapped[dict | None] = mapped_column(JSON, default=None)
    device: Mapped[str | None] = mapped_column(String(200), default=None)  # what it ran on

    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class TrainedModel(Base, TimestampMixin):
    """A model that can make suggestions in a project's annotation workspace: the result of a training
    run (its files stay in the run's folder), or one trained elsewhere and added as a file (kept in
    ``<training dir>/models/<folder>``)."""

    __tablename__ = "trained_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("training_runs.id", ondelete="CASCADE"), default=None, index=True)
    name: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(30))
    recipe_id: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20), default="run", server_default="run")  # run | import
    # The classes it knows. Trained here: by the project's class ids, [{"id", "name"}]. Added from
    # elsewhere: its own classes in its own order, [{"index", "name"}], with ``class_map`` saying which
    # class of the project each one is ({"0": 12, "1": null}; null: not used here).
    classes: Mapped[list] = mapped_column(JSON, default=list)
    class_map: Mapped[dict | None] = mapped_column(JSON, default=None)
    # Its score when it was made, e.g. {"metric": "ap50", "val": 0.81, "test": 0.78}.
    score: Mapped[dict | None] = mapped_column(JSON, default=None)
    # Where its files are, relative to the training folder: the recipe's code and the weights.
    code_path: Mapped[str] = mapped_column(String(300))
    weights_path: Mapped[str] = mapped_column(String(300))


class Suggestion(Base, TimestampMixin):
    """A shape a model proposes in a patch. It is not an annotation -- no export or statistic sees it --
    until a person accepts it, which makes an annotation of it."""

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id", ondelete="CASCADE"), index=True)
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("trained_models.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("annotation_classes.id", ondelete="SET NULL"), default=None)
    type: Mapped[str] = mapped_column(String(20))
    coordinates_patch_local: Mapped[list] = mapped_column(JSON)
    coordinates_level0: Mapped[list] = mapped_column(JSON)
    score: Mapped[float] = mapped_column()  # how sure the model is, 0-1
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | accepted | rejected
    # The annotation it became, once accepted.
    annotation_id: Mapped[int | None] = mapped_column(ForeignKey("geometry_annotations.id", ondelete="SET NULL"), default=None)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
