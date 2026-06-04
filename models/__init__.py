"""Typed data models for EasyQC."""

from models.project import Project, ProjectRegistry
from models.qcmodule import QCModule, Score, Tag
from models.rating import Rating

__all__ = [
    "Project",
    "ProjectRegistry",
    "QCModule",
    "Score",
    "Tag",
    "Rating",
]
