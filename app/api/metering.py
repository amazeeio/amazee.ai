from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta, UTC
from typing import Optional, List
from app.core.config import settings
import logging
from app.schemas.models import MeteringResponse
from openmeter import Client

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()

async def get_openmeter_client():
    client = Client(
        api_key=settings.OPENMETER_API_KEY,
        endpoint=settings.OPENMETER_URL
    )
    yield client

@router.get("/metering/meters", response_model=List[dict])
def list_meters(
    client: Client = Depends(get_openmeter_client)
):
    """
    List all available meters in OpenMeter.
    """
    try:
        logger.info("Fetching list of meters from OpenMeter")
        meters = client.list_meters()
        logger.info("Successfully retrieved meters from OpenMeter")
        logger.debug("Meters data: %s", meters)
        return meters
    except Exception as e:
        logger.error("Error occurred while fetching meters: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch meters from OpenMeter: {str(e)}"
        )

@router.get("/metering/usage", response_model=MeteringResponse)
def get_metering_usage(
    start_time: str = Query(..., description="Start time for the metering data (ISO format, e.g. 2024-03-01T00:00:00Z)"),
    end_time: Optional[str] = Query(None, description="End time for the metering data (ISO format, e.g. 2024-03-01T00:00:00Z). If not provided, defaults to current time."),
    meter_id: str = Query("completion_tokens", description="The identifier of the meter to query"),
    window_size: str = Query("HOUR", description="The time window for aggregation (MINUTE, HOUR, or DAY)"),
    client: Client = Depends(get_openmeter_client)
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
            logger.info("No end_time provided, using current time: %s", end_dt)

        logger.info("Fetching metering data from OpenMeter - From: %s, To: %s, Meter: %s, Window: %s",
                   start_dt, end_dt, meter_id, window_size)

        # Use OpenMeter client to query meter data
        data = client.query_meter(
            meter_id_or_slug=meter_id,
            from_parameter=start_dt,
            to=end_dt,
            window_size=window_size
        )

        logger.info("Successfully retrieved metering data from OpenMeter")
        logger.debug("Response data: %s", data)

        # Add meter_id to the response data
        data["meter_id"] = meter_id

        return MeteringResponse(**data)
    except ValueError as e:
        logger.error("Invalid datetime format: %s", str(e))
        raise HTTPException(
            status_code=400,
            detail=f"Invalid datetime format. Please use ISO format (e.g. 2024-03-01T00:00:00Z): {str(e)}"
        )
    except Exception as e:
        logger.error("Error occurred while fetching metering data: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch metering data from OpenMeter: {str(e)}"
        )