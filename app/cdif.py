"""The survey as a CDIF file: schema.org JSON-LD after the Cross-Domain Interoperability Framework (CDIF 1.1,
CODATA; profiles Core, Discovery and Data Description), for catalogues across disciplines (EOSC, data search engines).

CDIF describes a data set for finding it (schema.org Dataset: title, team with ORCID, licence, period, country,
language, keywords, file) and its variables for using it: every column of the codebook as a schema:PropertyValue that
is also a DDI-CDI InstanceVariable, with its label, its kind of values and, for answer codes, the codes and labels as
schema:DefinedTerm (Ralf 2026-10-27, after the DDI Lifecycle 4.0 export of app/ddi.py). CDIF has no profile for a
questionnaire; that stays in the DDI file.

Profile URIs, property names and the shape of the records follow the examples of the CDIF profiles (GitHub
Cross-Domain-Interoperability-Framework, profile-discovery and profile-datadescription, release v1.1.1, CC BY 4.0).
public=True: the file of the Zenodo and Coscine packages, describing the columns of their codebook.csv without the
wording of instruments that have no general publication permission (export.codebook_rows).
"""
import json

from app import brand

CONTEXT = {"schema": "http://schema.org/", "dcterms": "http://purl.org/dc/terms/", "dcat": "http://www.w3.org/ns/dcat#",
           "cdi": "http://ddialliance.org/Specification/DDI-CDI/1.0/RDF/", "cdif": "https://w3id.org/cdif/"}
PROFILES = ("https://w3id.org/cdif/core/1.1", "https://w3id.org/cdif/discovery/1.1",
            "https://w3id.org/cdif/data_description/1.1")
LICENCES = {"cc-by-4.0": ("Creative Commons Attribution 4.0 International", "https://creativecommons.org/licenses/by/4.0/"),
            "cc-by-sa-4.0": ("Creative Commons Attribution-ShareAlike 4.0 International",
                             "https://creativecommons.org/licenses/by-sa/4.0/"),
            "cc-by-nc-4.0": ("Creative Commons Attribution-NonCommercial 4.0 International",
                             "https://creativecommons.org/licenses/by-nc/4.0/"),
            "cc-zero": ("CC0 1.0 Universal", "https://creativecommons.org/publicdomain/zero/1.0/")}
# the kind of values of a column, as the DDI file names it (app/ddi._kind), in schema.org / XML Schema words
DATA_TYPES = {"code": "xsd:string", "number": "xsd:decimal", "date": "xsd:dateTime", "text": "xsd:string"}


def licence(survey):
    """(name, url) of the data licence of the consent, or None (app/zenodo.license_id)."""
    from app import zenodo

    try:
        return LICENCES.get(zenodo.license_id(survey))
    except zenodo.ZenodoError:
        return None


def build(survey, lang=None, public=False, open_answers=False):
    """The CDIF JSON-LD of the survey as bytes (UTF-8)."""
    from app import ddi, export
    from app.links import participant_url

    url = participant_url(survey)
    doi = ((getattr(survey, "zenodo", None) or {}).get("doi") or "").strip()
    ident = f"https://doi.org/{doi}" if doi else url
    langs = ddi._languages(survey)
    plan = ddi._plan(survey)
    sample = plan.get("sample") or {}
    record = {
        "@context": CONTEXT,
        "@id": ident,
        "@type": ["schema:Dataset"],
        "schema:name": survey.title,
        "schema:identifier": ident,
        "schema:url": url,
        "schema:inLanguage": langs[0] if len(langs) == 1 else langs,
    }
    description = " ".join((survey.description or "").split())
    if description:
        record["schema:description"] = description
    creators = []
    for m in survey.members:
        if not m.display_name:
            continue
        person = {"@type": ["schema:Person"], "schema:name": m.display_name}
        if m.orcid_id:
            person["@id"] = f"https://orcid.org/{m.orcid_id}"
        if m.affiliation:
            person["schema:affiliation"] = {"@type": ["schema:Organization"], "schema:name": m.affiliation}
        creators.append(person)
    if creators:
        record["schema:creator"] = {"@list": creators}
    lic = licence(survey)
    if lic:
        record["schema:license"] = [{"@type": ["schema:CreativeWork"], "schema:name": lic[0], "schema:url": lic[1]}]
    keywords = [k for k in [getattr(survey, "project", None)] if k and k != "None"]
    if keywords:
        record["schema:keywords"] = keywords
    if survey.opened_at:
        end = f"{survey.closed_at:%Y-%m-%d}" if survey.closed_at else ".."
        record["schema:temporalCoverage"] = [f"{survey.opened_at:%Y-%m-%d}/{end}"]
    country, region = sample.get("country") or "", sample.get("region") or ""
    if country or region:
        from app import geoip

        place = {"@type": ["schema:Place"], "schema:name": ", ".join(
            x for x in (geoip.country_name(country, "en") if country else "", region) if x)}
        if country:
            place["schema:identifier"] = country  # ISO 3166-1 alpha-2
        record["schema:spatialCoverage"] = [place]
    methods = [term for term in ddi.modes(survey)]
    if methods:
        record["schema:measurementTechnique"] = [
            {"@type": ["schema:DefinedTerm"], "schema:name": term, "schema:termCode": term,
             "schema:inDefinedTermSet": f"{ddi.CV}ModeOfCollection/{ddi.VOCABULARIES['ModeOfCollection']}",
             "schema:identifier": f"{ddi.CV}ModeOfCollection/{ddi.VOCABULARIES['ModeOfCollection']}/"
                                  f"{ddi.TERMS[('ModeOfCollection', term)]}"} for term in methods]
    if (sample.get("population") or "").strip():
        record["schema:audience"] = {"@type": ["schema:Audience"], "schema:audienceType": sample["population"].strip()}
    record["schema:variableMeasured"] = _variables(survey, lang, public, open_answers, ident)
    record["schema:distribution"] = [{"@type": ["schema:DataDownload"], "schema:name": "data.csv",
                                      "schema:encodingFormat": ["text/csv"],
                                      "schema:contentUrl": "data.csv" if public else export.filename(survey, "data")}]
    record["schema:isBasedOn"] = {"@type": ["schema:SoftwareApplication"], "schema:name": brand.name(),
                                  "schema:url": brand.public_base()}
    record["schema:subjectOf"] = {
        "@id": f"{ident}#metadata", "@type": ["schema:Dataset"],
        "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
        "schema:about": {"@id": ident},
        "dcterms:conformsTo": [{"@id": p} for p in PROFILES],
        "schema:sdPublisher": {"@type": ["schema:Organization"], "schema:name": brand.name()}}
    return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")


def _variables(survey, lang, public, open_answers, ident):
    """Every column of the codebook as a CDIF InstanceVariable."""
    from app import ddi

    column_item = ddi._columns_by_item(survey)
    out = []
    for row in ddi._codebook(survey, lang, public, open_answers):
        col, label, notes = row[0], row[1] or row[0], row[4]
        item = column_item.get(col)
        pairs = ddi._pairs(row)
        kind = "code" if pairs else ddi._kind(row, item, col)
        var = {"@type": ["schema:PropertyValue", "cdi:InstanceVariable"], "@id": f"{ident}#variable-{col}",
               "schema:name": col, "cdif:name": [col], "schema:description": label,
               "cdif:physicalDataType": DATA_TYPES[kind]}
        if notes:
            var["cdif:definition"] = notes
        if pairs:
            missing = {str(m) for m in item.kind.missing_codes(item, ddi._base_column(item, col))} if item else set()
            var["schema:valueReference"] = [
                {"@type": ["schema:DefinedTerm"], "schema:termCode": code, "schema:name": text,
                 **({"schema:description": "missing"} if code in missing else {})} for code, text in pairs]
        out.append(var)
    return out
