import logging
import pathlib
from datetime import datetime

import numpy as np
import pandas as pd
import xarray as xr

import georinex as gr
from .util import split_time_range

logger = logging.getLogger(__name__)


def _load_period(args):
    """Helper function to load a single time period from a RINEX file."""
    start, end, path, use, verbose = args

    # Convert numpy datetime64 to Python datetime properly
    if isinstance(start, np.datetime64):
        # Convert to datetime using datetime64.astype(datetime)
        start_dt = pd.Timestamp(start).floor('us').to_pydatetime()
    else:
        start_dt = start

    if isinstance(end, np.datetime64):
        end_dt = pd.Timestamp(end).floor('us').to_pydatetime()
    else:
        end_dt = end

    if verbose:
        logger.info(f"Loading period: {start_dt} to {end_dt}")

    result = gr.load(path, use=use, tlim=(start_dt, end_dt), fast=True)

    return result


def load_parallel(
        rinex_path: str,
        processes: int | None = None,
        use: set[str] | None = None,
        tlim: tuple[datetime, datetime] | None = None,
        verbose: bool = False
) -> xr.Dataset:
    """
    Load a RINEX observation file in parallel by splitting the time range and processing chunks separately.

    Parameters:
        rinex_path (str): The path to the RINEX file
        processes (int, optional): Number of processes to use. Defaults to number of CPU cores.
        use (set[str], optional): Single character(s) for constellation type filter.
                            G=GPS, R=GLONASS, E=Galileo, S=SBAS, J=QZSS, C=BeiDou, I=IRNSS
        tlim (tuple[datetime, datetime], optional): Time range to load from the file.
        verbose (bool, optional): If True, print detailed information about the loading process.

    Returns:
        xr.Dataset: The combined dataset from all time chunks
    """
    from georinex.obs3 import obstime3
    from georinex.obs2 import obstime2
    from georinex.rio import rinexinfo
    import multiprocessing

    path = pathlib.Path(rinex_path)

    if not processes:
        processes = multiprocessing.cpu_count()

    info = rinexinfo(path)
    rinex_type = info["rinextype"]
    version = info["version"]

    if rinex_type != "obs":
        raise ValueError("Only observation files are supported for parallel loading.")

    major_version = int(version)

    if tlim:
        start_time = tlim[0]
        end_time = tlim[1]
        if verbose:
            logger.info(f"Using specified time limits: {start_time} to {end_time}")
    else:
        # Get file time range
        if major_version == 3:
            times = obstime3(path, verbose=verbose)
        elif major_version == 2:
            times = obstime2(path, verbose=verbose)
        else:
            raise ValueError(f"Unsupported RINEX version: {version}")

        # Add a second diff to avoid dropping time steps
        start_time = times[0]
        end_time = times[-1]
        if verbose:
            logger.info(f"File time range: {start_time} to {end_time}")

    # Split time range into periods
    # Subtract 1 microsecond to avoid overlap
    timestamps = split_time_range(start_time, end_time, processes)
    periods = []
    for i in range(len(timestamps) - 1):
        if i == len(timestamps) - 2:  # Last period
            periods.append((timestamps[i], timestamps[i + 1]))  # Use exact end time
        else:
            periods.append((timestamps[i], timestamps[i + 1] - np.timedelta64(1, "us")))  # Avoid overlap

    if verbose:
        logger.info(f"Processing file {path} with {processes} processes")
        logger.info(f"Split into {len(periods)} time periods")

    # Prepare arguments for the worker function
    args_list = [(period[0], period[1], path, use, verbose) for period in periods]

    # Use multiprocessing to load all periods
    with multiprocessing.Pool(processes=processes) as pool:
        results = pool.map(_load_period, args_list)

    # Combine all datasets along the time dimension
    if results:
        combined_ds = xr.concat(
            results,
            dim="time",
        )

        if verbose:
            logger.info(f"Successfully combined {len(results)} datasets")

        return combined_ds
    else:
        raise ValueError("No data was loaded from the RINEX file")
