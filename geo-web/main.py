import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.cloud import storage


app = FastAPI(title="GEO Web - Brand Context Intake", version="1.0.0")
templates = Jinja2Templates(directory="templates")

GCS_INPUT_BUCKET = os.environ.get("GCS_INPUT_BUCKET", "geo-inputs")
DEFAULT_CLIENT_ID = os.environ.get("DEFAULT_CLIENT_ID", "client_001")

def make_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"run_{timestamp}_{suffix}"


def clean_lines(value: str) -> list[str]:
    if not value:
        return []

    items = []

    for line in value.splitlines():
        line = line.strip()

        if not line:
            continue

        if "," in line:
            items.extend([item.strip() for item in line.split(",") if item.strip()])
        else:
            items.append(line)

    return items


def bullet_list(value: str) -> str:
    items = clean_lines(value)
    return "\n".join([f"- {item}" for item in items]) if items else "- Not provided"


def validate_required_text(field_name: str, value: str, min_len: int = 2, max_len: int = 5000) -> str:
    value = (value or "").strip()

    if len(value) < min_len:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is required and must be at least {min_len} characters.",
        )

    if len(value) > max_len:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must not exceed {max_len} characters.",
        )

    return value


def validate_url(value: str) -> str:
    value = validate_required_text("Website URL", value, min_len=5, max_len=1000)

    if not (value.startswith("http://") or value.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="Website URL must start with http:// or https://",
        )

    return value


def validate_list_field(field_name: str, value: str) -> str:
    value = validate_required_text(field_name, value, min_len=2, max_len=5000)

    if not clean_lines(value):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must contain at least one value.",
        )

    return value


def build_brand_context_md(
    brand_name: str,
    website_url: str,
    industry: str,
    description: str,
    competitor_list: str,
    aliases: str,
    regions: str,
) -> str:
    return f"""# Brand Context

## Brand Name
{brand_name}

## Website URL
{website_url}

## Industry
{industry}

## Description
{description}

## Competitor List
{bullet_list(competitor_list)}

## Aliases
{bullet_list(aliases)}

## Regions
{bullet_list(regions)}
"""


def upload_text_to_gcs(
    bucket_name: str,
    object_name: str,
    content: str,
    content_type: str = "text/markdown",
) -> None:
    if not bucket_name:
        raise HTTPException(
            status_code=500,
            detail="GCS_INPUT_BUCKET environment variable is not configured.",
        )

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(content, content_type=content_type)


@app.get("/health")
def health():
    return {"status": "ok", "service": "geo-web-brand-context-intake"}


@app.get("/", response_class=HTMLResponse)
def form_page(request: Request):
    return templates.TemplateResponse("brand_context_form.html", {"request": request})


@app.post("/brand-context")
def create_brand_context(
    client_id: str = Form(DEFAULT_CLIENT_ID),
    brand_name: str = Form(...),
    website_url: str = Form(...),
    industry: str = Form(...),
    description: str = Form(...),
    competitor_list: str = Form(...),
    aliases: str = Form(...),
    regions: str = Form(...),
):
    brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
    website_url = validate_url(website_url)
    industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
    description = validate_required_text("Description", description, min_len=20, max_len=5000)
    competitor_list = validate_list_field("Competitor List", competitor_list)
    aliases = validate_list_field("Aliases", aliases)
    regions = validate_list_field("Regions", regions)

    safe_client_id = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id.strip() or DEFAULT_CLIENT_ID)
    run_id = make_run_id()

    brand_context_md = build_brand_context_md(
        brand_name=brand_name,
        website_url=website_url,
        industry=industry,
        description=description,
        competitor_list=competitor_list,
        aliases=aliases,
        regions=regions,
    )

    object_path = f"{safe_client_id}/{run_id}/brand_context.md"

    upload_text_to_gcs(
        bucket_name=GCS_INPUT_BUCKET,
        object_name=object_path,
        content=brand_context_md,
        content_type="text/markdown",
    )

    return JSONResponse(
        {
            "status": "success",
            "message": "brand_context.md created and uploaded.",
            "bucket": GCS_INPUT_BUCKET,
            "object_path": object_path,
            "gcs_uri": f"gs://{GCS_INPUT_BUCKET}/{object_path}",
            "client_id": safe_client_id,
            "run_id": run_id,
            "note": "client_id and run_id are not written inside brand_context.md.",
        }
    )


@app.post("/brand-context/preview")
def preview_brand_context(
    brand_name: str = Form(...),
    website_url: str = Form(...),
    industry: str = Form(...),
    description: str = Form(...),
    competitor_list: str = Form(...),
    aliases: str = Form(...),
    regions: str = Form(...),
):
    brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
    website_url = validate_url(website_url)
    industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
    description = validate_required_text("Description", description, min_len=20, max_len=5000)
    competitor_list = validate_list_field("Competitor List", competitor_list)
    aliases = validate_list_field("Aliases", aliases)
    regions = validate_list_field("Regions", regions)

    return {
        "brand_context_md": build_brand_context_md(
            brand_name=brand_name,
            website_url=website_url,
            industry=industry,
            description=description,
            competitor_list=competitor_list,
            aliases=aliases,
            regions=regions,
        )
    }
