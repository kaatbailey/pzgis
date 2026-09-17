from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from pathlib import Path
import tempfile
import subprocess
import json
import zipfile
import shutil
import uuid


app = FastAPI(title="PZ GIS")


PZGIS_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = PZGIS_DIR / "fetch_gis.py"


class GISRequest(BaseModel):
    country: str
    geojson: dict


# Countries are deliberately explicit here.
# The current pzgis implementation has two actual processing paths:
#
#   United States -> US government GIS sources
#   everything else -> OpenStreetMap / Overpass
#
# We can expand this mapping later when pzgis gains
# country-specific processors.
COUNTRY_SOURCES = {
    "United States": "us",
    "Japan": "osm",
    "Canada": "osm",
    "Mexico": "osm",
    "Brazil": "osm",
    "Germany": "osm",
    "France": "osm",
    "United Kingdom": "osm",
    "Australia": "osm",
    "New Zealand": "osm",
    "Other": "osm",
}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


@app.post("/api/process")
def process_gis(request: GISRequest):

    if not FETCH_SCRIPT.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Could not find pzgis processor: {FETCH_SCRIPT}"
        )

    if request.country not in COUNTRY_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported country: {request.country}"
        )

    # Validate that this is actually JSON-ish GeoJSON.
    if not isinstance(request.geojson, dict):
        raise HTTPException(
            status_code=400,
            detail="GeoJSON must be a JSON object."
        )

    if "type" not in request.geojson:
        raise HTTPException(
            status_code=400,
            detail="GeoJSON is missing its 'type' property."
        )

    source = COUNTRY_SOURCES[request.country]

    job_id = uuid.uuid4().hex

    # Everything for this request lives in a temporary directory.
    work_dir = Path(tempfile.mkdtemp(prefix=f"pzgis_{job_id}_"))

    try:
        area_file = work_dir / "area.geojson"

        area_file.write_text(
            json.dumps(request.geojson, indent=2),
            encoding="utf-8"
        )

        # Run the existing pzgis program.
        #
        # US:
        #     python fetch_gis.py area.geojson output/
        #
        # OSM:
        #     python fetch_gis.py area.geojson output/ --source osm
        #
        command = [
            "python3",
            str(FETCH_SCRIPT),
            str(area_file),
            str(work_dir),
        ]

        if source == "osm":
            command.append("--source")
            command.append("osm")

        result = subprocess.run(
            command,
            cwd=PZGIS_DIR,
            capture_output=True,
            text=True,
            timeout=15 * 60,  # 15 minutes
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "pzgis failed",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )

        # Find whatever GIS layers pzgis actually produced.
        output_files = []

        for filename in [
            "buildings.geojson",
            "roads.geojson",
            "water.geojson",
            "landuse.geojson",
        ]:
            path = work_dir / filename

            if path.exists():
                output_files.append(path)

        if not output_files:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "pzgis completed but produced no GIS files.",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )

        # Package the results.
        zip_path = work_dir / "pzgis_output.zip"

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED
        ) as archive:

            # Include the original input.
            archive.write(
                area_file,
                "area.geojson"
            )

            # Include generated GIS layers.
            for path in output_files:
                archive.write(
                    path,
                    path.name
                )

            # Include the processor log.
            log_path = work_dir / "pzgis.log"

            log_path.write_text(
                result.stdout +
                "\n\n===== STDERR =====\n\n" +
                result.stderr,
                encoding="utf-8"
            )

            archive.write(
                log_path,
                "pzgis.log"
            )

        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"pzgis-{request.country.replace(' ', '_')}.zip",
            background=None,
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="pzgis took longer than 15 minutes and was stopped."
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>PZ GIS</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background: #111827;
            color: #e5e7eb;
            font-family:
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
        }

        .container {
            max-width: 1000px;
            margin: 50px auto;
            padding: 0 20px;
        }

        h1 {
            margin-bottom: 8px;
            color: white;
        }

        .subtitle {
            color: #9ca3af;
            margin-bottom: 30px;
        }

        .card {
            background: #1f2937;
            border: 1px solid #374151;
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 20px;
        }

        label {
            display: block;
            font-weight: 600;
            margin-bottom: 8px;
        }

        select,
        textarea {
            width: 100%;
            background: #111827;
            color: #e5e7eb;
            border: 1px solid #4b5563;
            border-radius: 6px;
            padding: 12px;
            font-family: monospace;
        }

        select {
            font-family: inherit;
            font-size: 16px;
        }

        textarea {
            min-height: 350px;
            resize: vertical;
            font-size: 13px;
        }

        .field {
            margin-bottom: 24px;
        }

        button {
            background: #2563eb;
            border: none;
            color: white;
            padding: 12px 22px;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }

        button:hover {
            background: #1d4ed8;
        }

        button:disabled {
            background: #4b5563;
            cursor: wait;
        }

        .status {
            margin-top: 20px;
            padding: 12px;
            border-radius: 6px;
            display: none;
        }

        .status.info {
            display: block;
            background: #172554;
            color: #bfdbfe;
        }

        .status.error {
            display: block;
            background: #450a0a;
            color: #fecaca;
            white-space: pre-wrap;
        }

        .status.success {
            display: block;
            background: #052e16;
            color: #bbf7d0;
        }

        .hint {
            margin-top: 8px;
            color: #9ca3af;
            font-size: 13px;
        }

        .source {
            margin-top: 10px;
            color: #9ca3af;
            font-size: 14px;
        }

        code {
            color: #93c5fd;
        }
    </style>
</head>

<body>

<div class="container">

    <h1>PZ GIS</h1>

    <div class="subtitle">
        Generate Project Zomboid GIS data from a GeoJSON area.
    </div>

    <div class="card">

        <div class="field">

            <label for="country">
                Country
            </label>

            <select id="country">

                <option value="United States">
                    United States
                </option>

                <option value="Japan">
                    Japan
                </option>

                <option value="Canada">
                    Canada
                </option>

                <option value="Mexico">
                    Mexico
                </option>

                <option value="Brazil">
                    Brazil
                </option>

                <option value="Germany">
                    Germany
                </option>

                <option value="France">
                    France
                </option>

                <option value="United Kingdom">
                    United Kingdom
                </option>

                <option value="Australia">
                    Australia
                </option>

                <option value="New Zealand">
                    New Zealand
                </option>

                <option value="Other">
                    Other
                </option>

            </select>

            <div class="source" id="sourceDescription"></div>

        </div>


        <div class="field">

            <label for="geojson">
                GeoJSON
            </label>

            <textarea
                id="geojson"
                placeholder='Paste your GeoJSON here...

Example:
{
  "type": "FeatureCollection",
  "features": [...]
}'></textarea>

            <div class="hint">
                Paste the GeoJSON containing the area you want to process.
            </div>

        </div>


        <button id="submitButton">
            Process GIS
        </button>

        <div id="status" class="status"></div>

    </div>

</div>


<script>

const country = document.getElementById("country");
const geojson = document.getElementById("geojson");
const button = document.getElementById("submitButton");
const status = document.getElementById("status");
const sourceDescription = document.getElementById("sourceDescription");


function updateSourceDescription() {

    if (country.value === "United States") {

        sourceDescription.textContent =
            "Uses USA Structures and Census TIGERweb.";

    } else {

        sourceDescription.textContent =
            "Uses OpenStreetMap / Overpass.";

    }
}


country.addEventListener(
    "change",
    updateSourceDescription
);


updateSourceDescription();


function showStatus(message, type) {

    status.className = "status " + type;
    status.textContent = message;

}


button.addEventListener("click", async () => {

    let parsedGeoJSON;

    // Parse GeoJSON before sending it to the server.
    try {

        parsedGeoJSON = JSON.parse(
            geojson.value
        );

    } catch (error) {

        showStatus(
            "The GeoJSON is not valid JSON.\n\n" +
            error.message,
            "error"
        );

        return;
    }


    if (!parsedGeoJSON.type) {

        showStatus(
            "The GeoJSON does not contain a 'type' property.",
            "error"
        );

        return;
    }


    button.disabled = true;

    showStatus(
        "Processing GIS data...\n\n" +
        "This can take several minutes for large areas.",
        "info"
    );


    try {

        const response = await fetch(
            "/api/process",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    country: country.value,
                    geojson: parsedGeoJSON
                })
            }
        );


        if (!response.ok) {

            let message;

            try {

                const data = await response.json();

                message =
                    typeof data.detail === "string"
                        ? data.detail
                        : JSON.stringify(data.detail, null, 2);

            } catch {

                message =
                    await response.text();

            }

            throw new Error(message);
        }


        const blob = await response.blob();

        const url = window.URL.createObjectURL(blob);

        const link = document.createElement("a");

        link.href = url;

        link.download =
            "pzgis-" +
            country.value.replaceAll(" ", "_") +
            ".zip";

        document.body.appendChild(link);

        link.click();

        link.remove();

        window.URL.revokeObjectURL(url);


        showStatus(
            "GIS processing complete. " +
            "Your ZIP file has been downloaded.",
            "success"
        );

    } catch (error) {

        showStatus(
            "Processing failed:\n\n" +
            error.message,
            "error"
        );

    } finally {

        button.disabled = false;

    }

});

</script>

</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
