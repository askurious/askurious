"""Researcher-side survey management, one module per tab (settings, people, data, field, results, ...).

All routes stay on the one blueprint "surveys" (base.bp), so URLs and endpoint names are the same as before the
split. Importing the modules below registers their routes; the names other code takes from `app.surveys` are
re-exported here.
"""
from app.surveys import base  # noqa: F401  (first: the blueprint)
from app.surveys import (contacts, cron, data, design, estimates, exports, field, links, people,  # noqa: F401
                         quality, results, seal, settings, status)
from app.surveys.base import (DATA_GET_ENDPOINTS, LANGUAGE_NAMES, SECTION_ENDPOINTS, _add_member,  # noqa: F401
                              _back_to, _my_projects, _remember_contact, _settings_conflict, _settings_saved,
                              _with_consent, apply_image_style, apply_languages, apply_participation, apply_project,
                              bp, get_survey, pool_counts, stored_pool)
from app.surveys.cron import cron_bp  # noqa: F401
from app.surveys.data import _data_page, csv_body, rds_body  # noqa: F401
from app.surveys.design import apply_theme_choice  # noqa: F401
from app.surveys.people import _search_people, people_keys_of, search_people, search_to_share  # noqa: F401
from app.surveys.results import undo_tops  # noqa: F401
from app.surveys.settings import ADMIN_STEP_NODES, section_role  # noqa: F401
