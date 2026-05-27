import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List

from google.cloud import storage


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brand-context-processor")


# Static defaults for testing.
# You can override these from Cloud Run Job environment variables.
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "geo-inputs")
INPUT_FILE = os.environ.get("INPUT_FILE", "client_001/run_001/brand_context.md")

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "geo-outputs")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "client_001/run_001/output.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_gcs_text(bucket_name: str, object_name: str) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    gcs_path = f"gs://{bucket_name}/{object_name}"
    logger.info("Reading input file: %s", gcs_path)

    if not blob.exists():
        raise FileNotFoundError(f"Input file not found: {gcs_path}")

    return blob.download_as_text()


def write_gcs_json(bucket_name: str, object_name: str, payload: dict) -> None:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    gcs_path = f"gs://{bucket_name}/{object_name}"
    logger.info("Writing output file: %s", gcs_path)

    blob.upload_from_string(
        json.dumps(payload, indent=2, ensure_ascii=False),
        content_type="application/json",
    )


def parse_markdown_sections(markdown_text: str) -> Dict[str, str]:
    pattern = r"^##\s+(.+?)\s*$"
    matches = list(re.finditer(pattern, markdown_text, flags=re.MULTILINE))

    sections: Dict[str, str] = {}

    for index, match in enumerate(matches):
        section_name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        sections[section_name] = markdown_text[start:end].strip()

    return sections


def parse_list_section(section_text: str) -> List[str]:
    if not section_text:
        return []

    values: List[str] = []

    for line in section_text.splitlines():
        line = line.strip()

        if not line:
            continue

        line = re.sub(r"^[-*]\s+", "", line)

        if "," in line and not line.lower().startswith(("http://", "https://")):
            values.extend([item.strip() for item in line.split(",") if item.strip()])
        else:
            values.append(line)

    return values


def normalize_region(value: str) -> str:
    key = value.strip().lower()

    region_map = {
        "us": "US",
        "u.s.": "US",
        "usa": "US",
        "united states": "US",
        "united states of america": "US",
        "india": "INDIA",
        "in": "INDIA",
        "bharat": "INDIA",
        "europe": "EUROPE",
        "eu": "EUROPE",
        "european union": "EUROPE",
        "japan": "JAPAN",
        "jp": "JAPAN",
        "asean": "ASEAN",
        "southeast asia": "ASEAN",
        "middle east": "MIDDLE_EAST",
        "uae": "MIDDLE_EAST",
        "united arab emirates": "MIDDLE_EAST",
    }

    return region_map.get(key, value.strip().upper())


def normalize_regions(regions: List[str]) -> List[str]:
    normalized = []

    for region in regions:
        normalized_region = normalize_region(region)

        if normalized_region and normalized_region not in normalized:
            normalized.append(normalized_region)

    return normalized


def process_brand_context(markdown_text: str) -> dict:
    sections = parse_markdown_sections(markdown_text)

    competitor_list = parse_list_section(sections.get("Competitor List", ""))
    aliases = parse_list_section(sections.get("Aliases", ""))
    regions_raw = parse_list_section(sections.get("Regions", ""))
    regions_normalized = normalize_regions(regions_raw)

    required_sections = [
        "Brand Name",
        "Website URL",
        "Industry",
        "Description",
        "Competitor List",
        "Aliases",
        "Regions",
    ]

    missing_sections = [
        section for section in required_sections
        if not sections.get(section, "").strip()
    ]

    return {
        "processing_status": "SUCCESS" if not missing_sections else "SUCCESS_WITH_WARNINGS",
        "generated_at": utc_now(),
        "source": {
            "input_bucket": INPUT_BUCKET,
            "input_file": INPUT_FILE,
            "input_gcs_uri": f"gs://{INPUT_BUCKET}/{INPUT_FILE}",
        },
        "destination": {
            "output_bucket": OUTPUT_BUCKET,
            "output_file": OUTPUT_FILE,
            "output_gcs_uri": f"gs://{OUTPUT_BUCKET}/{OUTPUT_FILE}",
        },
        "missing_sections": missing_sections,
        "brand": {
            "brand_name": sections.get("Brand Name", ""),
            "website_url": sections.get("Website URL", ""),
            "industry": sections.get("Industry", ""),
            "description": sections.get("Description", ""),
        },
        "competitors": competitor_list,
        "aliases": aliases,
        "regions": {
            "raw": regions_raw,
            "normalized": regions_normalized,
        },
        "summary": {
            "competitor_count": len(competitor_list),
            "alias_count": len(aliases),
            "region_count": len(regions_normalized),
            "sections_found": list(sections.keys()),
        },
    }


def main() -> None:
    logger.info("Brand context processor started")
    logger.info("INPUT_BUCKET=%s", INPUT_BUCKET)
    logger.info("INPUT_FILE=%s", INPUT_FILE)
    logger.info("OUTPUT_BUCKET=%s", OUTPUT_BUCKET)
    logger.info("OUTPUT_FILE=%s", OUTPUT_FILE)

    markdown_text = read_gcs_text(INPUT_BUCKET, INPUT_FILE)
    output_payload = process_brand_context(markdown_text)
    write_gcs_json(OUTPUT_BUCKET, OUTPUT_FILE, output_payload)

    logger.info("Brand context processor completed successfully")


if __name__ == "__main__":
    main()
