from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta, UTC
from typing import Optional, List
from app.core.config import settings
import logging
import httpx
from app.schemas.models import (
    MeteringResponse,
    Event,
    EventsResponse,
    OpenMeterPassthroughRequest
)
from pydantic import BaseModel

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()

class OpenMeterPassthroughRequest(BaseModel):
    endpoint: str
    data: dict
    method: str

async def get_openmeter_client():
    async with httpx.AsyncClient(
        base_url=settings.OPENMETER_URL,
        headers={
            "Authorization": f"Bearer {settings.OPENMETER_API_KEY}",
            "Accept": "application/json"
        }
    ) as client:
        yield client

@router.get("/metering/meters", response_model=List[dict])
async def list_meters(
    client: httpx.AsyncClient = Depends(get_openmeter_client)
):
    """
    List all available meters in OpenMeter.
    """
    try:
        logger.info("Fetching list of meters from OpenMeter")
        response = await client.get("/api/v1/meters")
        response.raise_for_status()
        meters = response.json()
        logger.info("Successfully retrieved meters from OpenMeter")
        logger.debug(f"Meters data: {meters}")
        return meters
    except httpx.HTTPError as e:
        logger.error(f"Error occurred while fetching meters: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch meters from OpenMeter: {str(e)}"
        )

@router.get("/metering/usage", response_model=MeteringResponse)
async def get_metering_usage(
    start_time: str = Query(..., description="Start time for the metering data (ISO format, e.g. 2024-03-01T00:00:00Z)"),
    end_time: Optional[str] = Query(None, description="End time for the metering data (ISO format, e.g. 2024-03-01T00:00:00Z). If not provided, defaults to current time."),
    meter_id: str = Query("completion_tokens", description="The identifier of the meter to query"),
    window_size: str = Query("HOUR", description="The time window for aggregation (MINUTE, HOUR, or DAY)"),
    client: httpx.AsyncClient = Depends(get_openmeter_client)
):
    """
    Get metering usage data from OpenMeter for a specified time range.
    If end_time is not provided, it defaults to the current time.
    """
    try:
        # Parse the datetime strings
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        else:
            end_dt = datetime.now(UTC)
            logger.info(f"No end_time provided, using current time: {end_dt}")

        logger.info(f"Fetching metering data from OpenMeter - From: {start_dt}, To: {end_dt}, Meter: {meter_id}, Window: {window_size}")

        # Use direct API call to query meter data
        response = await client.get(
            f"/api/v1/meters/{meter_id}/query",
            params={
                "from": start_dt.isoformat(),
                "to": end_dt.isoformat(),
                "windowSize": window_size
            }
        )
        response.raise_for_status()
        data = response.json()

        logger.info("Successfully retrieved metering data from OpenMeter")
        logger.debug(f"Response data: {data}")

        # Add meter_id to the response data
        modeled_data = {}
        modeled_data["meter_id"] = meter_id
        modeled_data["data"] = data["data"]
        modeled_data["windowSize"] = data["windowSize"]
        modeled_data["from_"] = data["from"]
        modeled_data["to"] = data["to"]

        return MeteringResponse(**modeled_data)
    except ValueError as e:
        logger.error(f"Invalid datetime format: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid datetime format. Please use ISO format (e.g. 2024-03-01T00:00:00Z): {str(e)}"
        )
    except httpx.HTTPError as e:
        logger.error(f"Error occurred while fetching metering data: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch metering data from OpenMeter: {str(e)}"
        )

@router.get("/metering/events", response_model=EventsResponse)
async def list_events(
    start_time: str = Query(..., description="Start time for the events (ISO format, e.g. 2024-03-01T00:00:00Z)"),
    end_time: Optional[str] = Query(None, description="End time for the events (ISO format, e.g. 2024-03-01T00:00:00Z). If not provided, defaults to current time."),
    event_type: Optional[str] = Query(None, description="Filter events by type"),
    subject: Optional[str] = Query(None, description="Filter events by subject"),
    source: Optional[str] = Query(None, description="Filter events by source"),
    page: int = Query(1, description="Page number for pagination"),
    size: int = Query(100, description="Number of events per page"),
    client: httpx.AsyncClient = Depends(get_openmeter_client)
):
    """
    List events from OpenMeter within a specified time period.
    """
    try:
        # Parse the datetime strings
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        if end_time:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        else:
            end_dt = datetime.now(UTC)
            logger.info(f"No end_time provided, using current time: {end_dt}")

        logger.info(f"Fetching events from OpenMeter - From: {start_dt}, To: {end_dt}, Type: {event_type}, Subject: {subject}, Source: {source}")

        # Build query parameters
        params = {
            "from": start_dt.isoformat(),
            "to": end_dt.isoformat(),
            "page": page,
            "size": size
        }

        if event_type:
            params["type"] = event_type
        if subject:
            params["subject"] = subject
        if source:
            params["source"] = source

        # Use direct API call to list events
        response = await client.get(
            "/api/v1/events",
            params=params
        )
        response.raise_for_status()
        events = response.json()

        logger.info(f"Successfully retrieved {len(events)} events from OpenMeter")
        logger.debug(f"Response data: {events}")

        return EventsResponse(events=events)
    except ValueError as e:
        logger.error(f"Invalid datetime format: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid datetime format. Please use ISO format (e.g. 2024-03-01T00:00:00Z): {str(e)}"
        )
    except httpx.HTTPError as e:
        logger.error(f"Error occurred while fetching events: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch events from OpenMeter: {str(e)}"
        )

@router.post("/metering/passthrough")
async def openmeter_passthrough(
    request: OpenMeterPassthroughRequest,
    client: httpx.AsyncClient = Depends(get_openmeter_client)
):
    """
    Passthrough endpoint for OpenMeter API requests.
    """
    try:
        logger.info(f"Received passthrough request: {request.model_dump()}")
        logger.info(f"Making {request.method} request to OpenMeter: {request.endpoint}")
        if request.method.upper() == "GET":
            response = await client.get(request.endpoint)
        elif request.method.upper() == "POST":
            response = await client.post(request.endpoint, json=request.data)
        elif request.method.upper() == "PUT":
            response = await client.put(request.endpoint, json=request.data)
        elif request.method.upper() == "DELETE":
            response = await client.delete(request.endpoint)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported HTTP method: {request.method}"
            )
        response.raise_for_status()

        # Try to parse as JSON, if it fails return the raw text
        try:
            return response.json()
        except ValueError:
            return {"raw_response": response.text}
    except httpx.HTTPError as e:
        logger.error(f"Error in OpenMeter passthrough: {str(e)}")
        if hasattr(e, 'response'):
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 500,
            detail=f"OpenMeter request failed: {str(e)}"
        )