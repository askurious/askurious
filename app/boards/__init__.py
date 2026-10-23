"""Dashboards of a survey with public links, one module per part (tiles, compute, render, pages, copying).

The blueprints stay "boards" and "board_share", so URLs and endpoint names are the same as before the
split.
"""
from app.boards import base  # noqa: F401  (first: the blueprint)
from app.boards import compute, copying, pages, render, tiles  # noqa: F401
from app.boards.base import (MAX_BOARDS, MAX_RING, MAX_TEXT, MAX_TILES, MIN_CELL, MIN_TILE, OTHERS,  # noqa: F401
                             TEXT_KINDS, TILE_MINS, UNLIMITED, VALIDITY, VIEWS, _num, _pct, bp,
                             coding_key, expiry, has_link, item_ref, min_tile_of, shape_of, share_bp,
                             speaking)
from app.boards.compute import _build, _frame, _path, build, forget, in_step, state, step_cutoff, stepped  # noqa: F401
from app.boards.copying import copy_to, remap, remap_split  # noqa: F401
from app.boards.pages import ensure_value_board, may_change, value_board, value_share_urls  # noqa: F401
from app.boards.render import (PALETTE, _rendered, _signed, donut, end_links, end_share, render_tiles,  # noqa: F401
                               settle_future)
from app.boards.tiles import (_classes, _common_groups, _compare, _importances, _join, _known, _ranks,  # noqa: F401
                              _reduce, _span, _words, candidates, classes, groupers, is_text,
                              merge_adjacent, merge_small, pool_of, split_vars, start_of,
                              survey_has_link, tiles_of, width_of)
