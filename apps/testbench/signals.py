"""Bump a change marker whenever bench data changes; open boards poll against it."""

import time

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from .models import Case, Result, Run, Section

MARKER_KEY = "testbench:marker"


def bump_marker(**kwargs):
    # ponytail: one marker for every application, so a change anywhere re-renders every
    # open board once. Key it per application if that ever shows up as load.
    marker = time.time_ns()
    cache.set(MARKER_KEY, marker, None)
    return marker


for model in (Section, Case, Run, Result):
    post_save.connect(bump_marker, sender=model, dispatch_uid=f"testbench-save-{model.__name__}")
    post_delete.connect(bump_marker, sender=model, dispatch_uid=f"testbench-delete-{model.__name__}")
