"""Sorts the test files into one folder per area (architecture concept 2026-10-12, package G).

Run from the repository root on a fresh main: `python tools/sort_tests.py` moves every tests/test_*.py into
tests/<area>/ with `git mv` (an area by the first rule of AREAS whose pattern matches the file name) and fixes what
points at the old places: imports (`from tests.test_x import`, `from tests import test_x`), dotted and slash paths
in the tests, tools, docs and code comments, and the paths a moved test computes from its own __file__. The helper
files (conftest.py, tsa_fake.py, *.json, *.js, data/) stay in tests/. A file already in a folder stays where it is;
so the script can run again after new tests came in. `--dry-run` only prints the plan.

A test file named after an audit or review whose name says nothing about the area gets no area: name it after what it\ntests. log.md and decisions.md keep the old paths: they tell what was then."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# (area, pattern on the file name without "test_" and ".py"); the first match counts
AREAS = [
    ("diary", r"^diary"),
    ("interview", r"^(interview|coding|voice|cr8_f_interview|pool_and_interview_files|audit10_kodieren|audit11_ki)"),
    ("live", r"^(live|nginx_live_folder)"),
    ("quotas", r"^(quota|quotas|audit9_r1_value_link_quotas|review_small_quotas|census|balance|assign|split|value_links)"),
    ("dashboards", r"^(board|boards|dashboard|review_small_dashboards|ux_audit5_boards|showcase)"),
    ("exports", r"^(export|spss|jamovi|rds|cho$|zenodo|transfer|survey_files|seal|public_packages|prereg|import_origins|"
                r"audit_j_exports|bibliography|citations|paper|audit13_papier|coscine|audit10_siegel|audit11_siegelpruefung|"
                r"open_columns|ids)"),
    ("privacy", r"^(erasure|retention|privacy|platform_erase|access_log|audit10_loeschung|audit10_datenschutztexte|"
                r"backup|trash|consent|cookies|data_access|repeat_marker)"),
    ("accounts", r"^(accounts|account_|passwords|twofactor|admin_2fa|sessions|sign_in|new_accounts|orcid|roles|rights|"
                 r"group|groups|groupvars|security_roles|review2_rechte|audit_b_accounts|review_small_accounts|"
                 r"audit11_zugaenge|audit12_v7_anmeldung|own_ai_key|edit_guard|showcase_rights|personal_code)"),
    ("analysis", r"^(results|acbc|cbc_results|conjoint|maxdiff|hb_|paired|holdouts|segments|simulator|sem|power|"
                 r"samplesize|weights|rsi|omega|scales|own_subscales|construct|ai_scales|ai_stats|estimate|choice_|"
                 r"compare|sample_|intervals|apa|table_notes|discounting|psm|calc|level_values|dual_response|"
                 r"semantic_profile|position_association|duration|pagetime|speeders|quality|data_quality|mathe6|"
                 r"audit_math|audit_inferenz|audit_results|audit_wahlmodelle|audit13_statistik|audit9_p7_auswertung|"
                 r"audit9b_q5_auswertung|review_small_results|ui_audit_2026_10_07_results|ux_audit5_results|compute|"
                 r"dummy_plan|plan_|start_plan|study_plan|designs|vignettes|stimulus|model_auto|ainstein|"
                 r"audit10_studienplan|audit10_wahl|ux_audit5_plan|ux_audit5_numbers|wizard)"),
    ("items", r"^(slider|dropdown|image_choice|image_mark|date_time|drill_down|map_marker|federal_state|plz_state|"
              r"postal_code|geolocation|geoip|constant_sum|other_option|likert_endpoints|highlight_cloze|side_by_side|"
              r"card_sort|ranking|iat|email_check|regex_exif|webp|element_|audit8_e_elemente|audit10_elemente|"
              r"formatting|richtext|uploads|audit_2026_10_09_uploads|audit_b_files|"
              r"semantic|item_pages|display)"),
    ("editor", r"^(editor|comment_shots|comments|autosave|undo|flow|conditions|item_condition|row_conditions|piping|carry|loops|loop_table|"
               r"linked|design_editor|duplicate|surveys|settings_page|field_changes|waves|tempo|external|"
               r"webservice|demo_webapp|answer_lock|audit13_antwortsperre|audit8_x_duplizieren|audit_b_duplicate|"
               r"audit_b_copies|review2_editor|review_small_editor|ux_audit7_editors|sem_drag|survey_invites|invites|"
               r"join_links|links|learning|experimental|ai_same_page|audit_b_paused|feedback_lock|test)"),
    ("participation", r"^(participate|participant_|panel|end_links|keep_page|paradata|language_|translations|limits|"
                      r"answer_limits|submissions|audit10_teilnahme|audit13_teilnahme|review2_teilnahme|"
                      r"audit_d_participation|ui7_participant|ux9_c_participants|ux_audit_participant|"
                      r"ui_participant_access|signed_links|hosts|audit10_abschluss|audit_g_concurrency)"),
    ("platform", r"^(migrations|schema|rolling|deploy|nginx|brand|rename|scaling|performance|security|hardening|"
                 r"static_files|url_names|containers|audit\d+b?_.*betrieb|audit\d+b?_.*performance|audit9_p2_server|"
                 r"audit9_p6_security|audit9b_q3_public|audit9b_q4_marke|audit9b_q1_admin|audit9b_q2_mail|"
                 r"review2_postgres|audit8_pg|audit8_b_deploy|audit9_p3_deploy|webservice_guard|database_admin|mail|"
                 r"admin_|ai_limit|ai_usage|blog|complaints|feedback|topic_feedback|handbook|audit11_robustheit|"
                 r"review2_codeleichen|limits)"),
    ("ui", r"^(ui|ux|theme|researcher_i18n|researcher_pages|glossary|accessibility|a11y|link_style|header|"
           r"flash_position|public|review2_oberflaeche|rights_overview)"),
]


def area_of(name):
    stem = name[len("test_"):-len(".py")]
    for area, pattern in AREAS:
        if re.search(pattern, stem):
            return area
    return None


def plan():
    out, missing = {}, []
    for f in sorted(TESTS.glob("test_*.py")):
        area = area_of(f.name)
        (missing.append(f.name) if area is None else out.__setitem__(f.name, area))
    return out, missing


def fix_own_paths(text):
    """A moved test sits one folder deeper: what it computes from __file__ gets one level more."""
    file_expr = r"((?:pathlib\.)?Path\(__file__\)(?:\.resolve\(\))?)"
    text = re.sub(file_expr + r"\.parents\[(\d+)\]", lambda m: f"{m.group(1)}.parents[{int(m.group(2)) + 1}]", text)
    text = re.sub(file_expr + r"\.parent\.parent", r"\1.parents[2]", text)
    text = re.sub(file_expr + r"\.parent\b", r"\1.parents[1]", text)
    text = re.sub(file_expr + r"\.with_name\(", r"\1.parents[1].joinpath(", text)
    text = re.sub(r"os\.path\.dirname\(os\.path\.dirname\((os\.path\.abspath\(__file__\)|__file__)\)\)",
                  r"os.path.dirname(os.path.dirname(os.path.dirname(\1)))", text)
    text = re.sub(r"(?<!dirname\()os\.path\.dirname\((os\.path\.abspath\(__file__\)|__file__)\)(?!\))",
                  r"os.path.dirname(os.path.dirname(\1))", text)
    text = text.replace('os.path.join(os.path.dirname(__file__), "..",', 'os.path.join(os.path.dirname(__file__), "..", "..",')
    return text


def fix_imports(text, moved):
    """`from tests import test_a, test_b as b` per module; dotted and bare module names to their new place."""
    def split(m):
        indent, names = m.group(1), [n.strip() for n in m.group(2).split(",")]
        lines = []
        for n in names:
            mod = n.split(" as ")[0].strip()
            pkg = f"tests.{moved[mod + '.py']}" if mod + ".py" in moved else "tests"
            lines.append(f"{indent}from {pkg} import {n}")
        return "\n".join(lines)

    text = re.sub(r"^([ \t]*)from tests import ([\w ,]+)$", split, text, flags=re.M)
    for name, area in moved.items():
        mod = name[:-3]
        text = re.sub(rf"\btests\.{mod}\b", f"tests.{area}.{mod}", text)
        text = re.sub(rf"^([ \t]*)from {mod} import", rf"\1from tests.{area}.{mod} import", text, flags=re.M)
    return text


def fix_slash_paths(text, moved):
    for name, area in moved.items():
        text = re.sub(rf"(?<![\w/])tests/{re.escape(name)}\b", f"tests/{area}/{name}", text)
    return text


def main(dry):
    moved, missing = plan()
    if missing:
        print("no area for:", ", ".join(missing))
        return 1
    by_area = {}
    for name, area in moved.items():
        by_area.setdefault(area, []).append(name)
    for area, names in sorted(by_area.items()):
        print(f"{area}: {len(names)}")
    if dry:
        return 0
    for name, area in moved.items():
        (TESTS / area).mkdir(exist_ok=True)
        subprocess.run(["git", "mv", f"tests/{name}", f"tests/{area}/{name}"], cwd=ROOT, check=True)
    for name, area in moved.items():
        path = TESTS / area / name
        path.write_text(fix_own_paths(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    texts = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".py", ".md", ".sh", ".cmd", ".ps1", ".toml",
                                                                           ".ini", ".cfg", ".yml", ".yaml", ".txt")
             and not any(part in (".git", ".venv", "vendor", "node_modules", "__pycache__", "instance") for part in p.parts)
             and p.name not in ("log.md", "decisions.md")]
    for path in texts:
        old = path.read_text(encoding="utf-8", errors="strict") if path.stat().st_size < 5_000_000 else None
        if old is None:
            continue
        new = fix_slash_paths(old, moved)
        if path.suffix == ".py":
            new = fix_imports(new, moved)
        if new != old:
            path.write_text(new, encoding="utf-8", newline="\n" if "\r\n" not in old else "\r\n")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
