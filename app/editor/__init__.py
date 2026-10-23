"""Questionnaire editor, one module per part (pages, elements, flow, preview, images, ...).

Structure (adding, removing, moving, codes, options, scale points) can only change in draft. Once a survey
has started, the same forms accept text corrections only. The blueprints stay "editor", "preview_share"
and "flow_page", so URLs and endpoint names are the same as before the split.
"""
from app.editor import base  # noqa: F401  (first: the blueprint)
from app.editor import ai_help, arrange, assets, duplicating, elements, flow, images, pages, preview, review, share  # noqa: F401
from app.editor.arrange import _draft_consent  # noqa: F401
from app.editor.base import (_is_consent_block, _lang, _renumber, _require_draft, _unique_code, bp,  # noqa: F401
                             column_head, palette_counts, preview_answer, soft_hyphens, type_counts)
from app.editor.elements import QUESTION_MAX  # noqa: F401
from app.editor.flow import flow_bp  # noqa: F401
from app.editor.preview import PRETEST_DAYS, _share_serializer, qr_svg, share_url  # noqa: F401
from app.editor.share import share_bp  # noqa: F401
