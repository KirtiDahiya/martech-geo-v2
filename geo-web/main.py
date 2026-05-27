import os
import re
from io import BytesIO

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse


app = FastAPI(title="GEO Web - Brand Context Intake", version="1.1.0")


# --------------------------------------------------
# Environment variables
# --------------------------------------------------

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "martech-497412")
REGION = os.environ.get("REGION", "asia-south2")

GCS_INPUT_BUCKET = os.environ.get("GCS_INPUT_BUCKET", "geo-inputs")
GCS_OUTPUT_BUCKET = os.environ.get("GCS_OUTPUT_BUCKET", "geo-output")

PROCESSOR_JOB_NAME = os.environ.get("PROCESSOR_JOB_NAME", "brand-context-processor")

DEFAULT_CLIENT_ID = os.environ.get("DEFAULT_CLIENT_ID", "client_001")


# --------------------------------------------------
# ID helpers
# --------------------------------------------------

def make_run_id() -> str:
    """
    Static run ID for testing.

    This ensures the input/output paths always remain:
    client_001/run_001/brand_context.md
    client_001/run_001/output.json

    For production, replace this with a timestamp/UUID-based run ID.
    """
    return "run_001"


def safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value.strip() or DEFAULT_CLIENT_ID)


# --------------------------------------------------
# Input parsing and validation
# --------------------------------------------------

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


def validate_required_text(
    field_name: str,
    value: str,
    min_len: int = 2,
    max_len: int = 5000,
) -> str:
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


# --------------------------------------------------
# brand_context.md builder
# --------------------------------------------------

def build_brand_context_md(
    brand_name: str,
    website_url: str,
    industry: str,
    description: str,
    competitor_list: str,
    aliases: str,
    regions: str,
) -> str:
    """
    Creates clean brand_context.md.

    Important:
    - No client_id
    - No run_id
    - No platform metadata
    """

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


# --------------------------------------------------
# GCS functions
# --------------------------------------------------

def upload_text_to_gcs(
    bucket_name: str,
    object_name: str,
    content: str,
    content_type: str = "text/markdown",
) -> str:
    """
    Upload text content to GCS.

    Lazy import is used so the app does not fail at startup if local env is incomplete.
    """

    if not bucket_name:
        raise HTTPException(
            status_code=500,
            detail="GCS_INPUT_BUCKET is not configured.",
        )

    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    blob.upload_from_string(content, content_type=content_type)

    return f"gs://{bucket_name}/{object_name}"


def download_gcs_file_as_bytes(bucket_name: str, object_name: str) -> bytes:
    """
    Download output file from GCS as bytes.
    """

    if not bucket_name:
        raise HTTPException(
            status_code=500,
            detail="GCS_OUTPUT_BUCKET is not configured.",
        )

    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    if not blob.exists():
        raise HTTPException(
            status_code=404,
            detail="Output file is not ready yet. Please try again after the processor job completes.",
        )

    return blob.download_as_bytes()


# --------------------------------------------------
# Cloud Run Job trigger
# --------------------------------------------------

def trigger_processor_job(
    input_bucket: str,
    input_file: str,
    output_bucket: str,
    output_file: str,
) -> str:
    """
    Trigger brand-context-processor Cloud Run Job.

    This removes the need to manually execute the job.
    """

    if not GCP_PROJECT_ID:
        raise HTTPException(
            status_code=500,
            detail="GCP_PROJECT_ID is not configured.",
        )

    from google.cloud.run_v2 import JobsClient, RunJobRequest

    client = JobsClient()

    job_name = (
        f"projects/{GCP_PROJECT_ID}/locations/{REGION}/jobs/{PROCESSOR_JOB_NAME}"
    )

    request = RunJobRequest(
        name=job_name,
        overrides={
            "container_overrides": [
                {
                    "env": [
                        {"name": "INPUT_BUCKET", "value": input_bucket},
                        {"name": "INPUT_FILE", "value": input_file},
                        {"name": "OUTPUT_BUCKET", "value": output_bucket},
                        {"name": "OUTPUT_FILE", "value": output_file},
                    ]
                }
            ]
        },
    )

    operation = client.run_job(request=request)
    return operation.operation.name


# --------------------------------------------------
# HTML form
# --------------------------------------------------

def form_html() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
  <title>GEO Brand Context Intake</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 950px; margin: 40px auto; line-height: 1.5; color: #222; }
    h1 { margin-bottom: 6px; }
    .subtitle { color: #666; margin-bottom: 24px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
    label { font-weight: bold; display: block; margin-top: 16px; }
    input, textarea { width: 100%; padding: 10px; margin-top: 6px; border: 1px solid #ccc; border-radius: 8px; font-size: 14px; box-sizing: border-box; }
    textarea { min-height: 90px; }
    button { margin-top: 24px; padding: 12px 18px; border: none; border-radius: 8px; cursor: pointer; font-size: 15px; background: #111827; color: white; }
    .hint { font-size: 12px; color: #666; margin-top: 4px; }
    code { background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }
  </style>
</head>
<body>
  <h1>GEO Brand Context Intake</h1>
  <p class="subtitle">
    Submit fields to create <code>brand_context.md</code>, trigger processor job, and generate downloadable output.
  </p>

  <div class="card">
    <form method="post" action="/brand-context">
      <label>Client ID</label>
      <input name="client_id" value="client_001">
      <div class="hint">Used only for GCS path. Not written inside brand_context.md.</div>

      <label>Brand Name *</label>
      <input name="brand_name" required value="ABC Mobility Components">

      <label>Website URL *</label>
      <input name="website_url" required value="https://www.abcmobilitycomponents.com">

      <label>Industry *</label>
      <input name="industry" required value="Automotive Components Manufacturing">

      <label>Description *</label>
      <textarea name="description" required>ABC Mobility Components is an automotive component manufacturer focused on body-in-white assemblies, sheet metal stampings, welded sub-assemblies, chassis structures, and EV-related structural components for passenger vehicle OEMs and Tier-1 automotive customers.</textarea>

      <label>Competitor List *</label>
      <textarea name="competitor_list" required>JBM Group
Autocomp Corporation
Gestamp India
Magna India
Bharat Forge auto components division</textarea>

      <label>Aliases *</label>
      <textarea name="aliases" required>ABC Mobility Components
ABC Mobility
ABC Components
ABC Auto Components
ABCMC</textarea>

      <label>Regions *</label>
      <textarea name="regions" required>India
IN
United States
US
U.S.
USA
Europe
EU
Japan
ASEAN
Middle East
UAE</textarea>

      <button type="submit">Create brand_context.md and Run Processor</button>
    </form>
  </div>
</body>
</html>
"""


def success_html(
    client_id: str,
    run_id: str,
    input_gcs_uri: str,
    output_gcs_uri: str,
    download_url: str,
    operation_name: str,
) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
  <title>GEO Run Started</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 850px; margin: 40px auto; line-height: 1.5; color: #222; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 24px; }}
    code {{ background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }}
    a.button {{ display: inline-block; margin-top: 20px; padding: 12px 18px; background: #111827; color: white; text-decoration: none; border-radius: 8px; }}
    .note {{ color: #666; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>GEO Processing Started</h1>

    <p><strong>Client ID:</strong> <code>{client_id}</code></p>
    <p><strong>Run ID:</strong> <code>{run_id}</code></p>

    <p><strong>Input file:</strong><br><code>{input_gcs_uri}</code></p>
    <p><strong>Expected output file:</strong><br><code>{output_gcs_uri}</code></p>

    <p><strong>Cloud Run Job operation:</strong><br><code>{operation_name}</code></p>

    <p class="note">
      The output may take a few seconds to become available. If download shows “not ready”,
      refresh after a short while.
    </p>

    <a class="button" href="{download_url}">Download Output JSON</a>
    <br><br>
    <a href="/">Create another run</a>
  </div>
</body>
</html>
"""


# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "geo-web-brand-context-intake",
        "project": GCP_PROJECT_ID,
        "region": REGION,
        "input_bucket": GCS_INPUT_BUCKET,
        "output_bucket": GCS_OUTPUT_BUCKET,
        "processor_job": PROCESSOR_JOB_NAME,
    }


@app.get("/", response_class=HTMLResponse)
def form_page(request: Request):
    return HTMLResponse(form_html())


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
    """
    Creates brand_context.md, uploads to GCS, and automatically triggers processor job.

    Static test paths:
    INPUT_FILE  = client_001/run_001/brand_context.md
    OUTPUT_FILE = client_001/run_001/output.json
    """

    brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
    website_url = validate_url(website_url)
    industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
    description = validate_required_text("Description", description, min_len=20, max_len=5000)
    competitor_list = validate_list_field("Competitor List", competitor_list)
    aliases = validate_list_field("Aliases", aliases)
    regions = validate_list_field("Regions", regions)

    safe_client_id = safe_id(client_id)
    run_id = make_run_id()

    input_file = f"{safe_client_id}/{run_id}/brand_context.md"
    output_file = f"{safe_client_id}/{run_id}/output.json"

    brand_context_md = build_brand_context_md(
        brand_name=brand_name,
        website_url=website_url,
        industry=industry,
        description=description,
        competitor_list=competitor_list,
        aliases=aliases,
        regions=regions,
    )

    input_gcs_uri = upload_text_to_gcs(
        bucket_name=GCS_INPUT_BUCKET,
        object_name=input_file,
        content=brand_context_md,
        content_type="text/markdown",
    )

    operation_name = trigger_processor_job(
        input_bucket=GCS_INPUT_BUCKET,
        input_file=input_file,
        output_bucket=GCS_OUTPUT_BUCKET,
        output_file=output_file,
    )

    output_gcs_uri = f"gs://{GCS_OUTPUT_BUCKET}/{output_file}"
    download_url = f"/download/{safe_client_id}/{run_id}"

    return HTMLResponse(
        success_html(
            client_id=safe_client_id,
            run_id=run_id,
            input_gcs_uri=input_gcs_uri,
            output_gcs_uri=output_gcs_uri,
            download_url=download_url,
            operation_name=operation_name,
        )
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


@app.get("/download/{client_id}/{run_id}")
def download_output(client_id: str, run_id: str):
    """
    Downloads generated output file from GCS.

    Expected:
    gs://geo-output/client_001/run_001/output.json
    """

    safe_client_id = safe_id(client_id)
    output_file = f"{safe_client_id}/{run_id}/output.json"

    file_bytes = download_gcs_file_as_bytes(
        bucket_name=GCS_OUTPUT_BUCKET,
        object_name=output_file,
    )

    return StreamingResponse(
        BytesIO(file_bytes),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}_output.json"'
        },
    )

# import os
# import re
# import uuid
# from datetime import datetime, timezone

# from fastapi import FastAPI, Form, HTTPException, Request
# from fastapi.responses import HTMLResponse, JSONResponse
# from fastapi.templating import Jinja2Templates
# from google.cloud import storage


# app = FastAPI(title="GEO Web - Brand Context Intake", version="1.0.0")
# templates = Jinja2Templates(directory="templates")

# GCS_INPUT_BUCKET = os.environ.get("GCS_INPUT_BUCKET", "geo-inputs")
# DEFAULT_CLIENT_ID = os.environ.get("DEFAULT_CLIENT_ID", "client_001")

# def make_run_id() -> str:
#     return "run_001"
#     # timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
#     # suffix = uuid.uuid4().hex[:8]
#     # return f"run_{timestamp}_{suffix}"


# def clean_lines(value: str) -> list[str]:
#     if not value:
#         return []

#     items = []

#     for line in value.splitlines():
#         line = line.strip()

#         if not line:
#             continue

#         if "," in line:
#             items.extend([item.strip() for item in line.split(",") if item.strip()])
#         else:
#             items.append(line)

#     return items


# def bullet_list(value: str) -> str:
#     items = clean_lines(value)
#     return "\n".join([f"- {item}" for item in items]) if items else "- Not provided"


# def validate_required_text(field_name: str, value: str, min_len: int = 2, max_len: int = 5000) -> str:
#     value = (value or "").strip()

#     if len(value) < min_len:
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} is required and must be at least {min_len} characters.",
#         )

#     if len(value) > max_len:
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} must not exceed {max_len} characters.",
#         )

#     return value


# def validate_url(value: str) -> str:
#     value = validate_required_text("Website URL", value, min_len=5, max_len=1000)

#     if not (value.startswith("http://") or value.startswith("https://")):
#         raise HTTPException(
#             status_code=400,
#             detail="Website URL must start with http:// or https://",
#         )

#     return value


# def validate_list_field(field_name: str, value: str) -> str:
#     value = validate_required_text(field_name, value, min_len=2, max_len=5000)

#     if not clean_lines(value):
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} must contain at least one value.",
#         )

#     return value


# def build_brand_context_md(
#     brand_name: str,
#     website_url: str,
#     industry: str,
#     description: str,
#     competitor_list: str,
#     aliases: str,
#     regions: str,
# ) -> str:
#     return f"""# Brand Context

# ## Brand Name
# {brand_name}

# ## Website URL
# {website_url}

# ## Industry
# {industry}

# ## Description
# {description}

# ## Competitor List
# {bullet_list(competitor_list)}

# ## Aliases
# {bullet_list(aliases)}

# ## Regions
# {bullet_list(regions)}
# """


# def upload_text_to_gcs(
#     bucket_name: str,
#     object_name: str,
#     content: str,
#     content_type: str = "text/markdown",
# ) -> None:
#     if not bucket_name:
#         raise HTTPException(
#             status_code=500,
#             detail="GCS_INPUT_BUCKET environment variable is not configured.",
#         )

#     client = storage.Client()
#     bucket = client.bucket(bucket_name)
#     blob = bucket.blob(object_name)
#     blob.upload_from_string(content, content_type=content_type)


# @app.get("/health")
# def health():
#     return {"status": "ok", "service": "geo-web-brand-context-intake"}


# @app.get("/", response_class=HTMLResponse)
# def form_page(request: Request):
#     return templates.TemplateResponse("brand_context_form.html", {"request": request})


# @app.post("/brand-context")
# def create_brand_context(
#     client_id: str = Form(DEFAULT_CLIENT_ID),
#     brand_name: str = Form(...),
#     website_url: str = Form(...),
#     industry: str = Form(...),
#     description: str = Form(...),
#     competitor_list: str = Form(...),
#     aliases: str = Form(...),
#     regions: str = Form(...),
# ):
#     brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
#     website_url = validate_url(website_url)
#     industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
#     description = validate_required_text("Description", description, min_len=20, max_len=5000)
#     competitor_list = validate_list_field("Competitor List", competitor_list)
#     aliases = validate_list_field("Aliases", aliases)
#     regions = validate_list_field("Regions", regions)

#     safe_client_id = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id.strip() or DEFAULT_CLIENT_ID)
#     run_id = make_run_id()

#     brand_context_md = build_brand_context_md(
#         brand_name=brand_name,
#         website_url=website_url,
#         industry=industry,
#         description=description,
#         competitor_list=competitor_list,
#         aliases=aliases,
#         regions=regions,
#     )

#     object_path = f"{safe_client_id}/{run_id}/brand_context.md"

#     upload_text_to_gcs(
#         bucket_name=GCS_INPUT_BUCKET,
#         object_name=object_path,
#         content=brand_context_md,
#         content_type="text/markdown",
#     )

#     return JSONResponse(
#         {
#             "status": "success",
#             "message": "brand_context.md created and uploaded.",
#             "bucket": GCS_INPUT_BUCKET,
#             "object_path": object_path,
#             "gcs_uri": f"gs://{GCS_INPUT_BUCKET}/{object_path}",
#             "client_id": safe_client_id,
#             "run_id": run_id,
#             "note": "client_id and run_id are not written inside brand_context.md.",
#         }
#     )


# @app.post("/brand-context/preview")
# def preview_brand_context(
#     brand_name: str = Form(...),
#     website_url: str = Form(...),
#     industry: str = Form(...),
#     description: str = Form(...),
#     competitor_list: str = Form(...),
#     aliases: str = Form(...),
#     regions: str = Form(...),
# ):
#     brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
#     website_url = validate_url(website_url)
#     industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
#     description = validate_required_text("Description", description, min_len=20, max_len=5000)
#     competitor_list = validate_list_field("Competitor List", competitor_list)
#     aliases = validate_list_field("Aliases", aliases)
#     regions = validate_list_field("Regions", regions)

#     return {
#         "brand_context_md": build_brand_context_md(
#             brand_name=brand_name,
#             website_url=website_url,
#             industry=industry,
#             description=description,
#             competitor_list=competitor_list,
#             aliases=aliases,
#             regions=regions,
#         )
#     }
