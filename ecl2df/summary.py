"""Provide a two-way Pandas DataFrame interface to Eclipse summary data (UNSMRY)"""
import logging
import datetime
from pathlib import Path

import dateutil.parser
import pandas as pd

from ecl.summary import EclSum

from .eclfiles import EclFiles
from . import parameters
from .common import write_dframe_stdout_file

logger = logging.getLogger(__name__)

PD_FREQ_MNEMONICS = {
    "monthly": "MS",
    "yearly": "YS",
    "daily": "D",
    "weekly": "W-MON",
}
"""Mapping from ecl2df custom offset strings to Pandas DateOffset strings.
See
https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#dateoffset-objects
"""  # noqa


def date_range(start_date, end_date, freq):
    """Wrapper for pandas.date_range to allow for extra ecl2df specific mnemonics
    'yearly', 'daily', 'weekly', mapped over to pandas DateOffsets.

    Args:
        start_date (datetime.date):
        end_date (datetime.date):
        freq (str): monthly, daily, weekly, yearly, or a Pandas date offset
            frequency.

    Returns:
        list of datetimes
    """
    if freq in PD_FREQ_MNEMONICS:
        freq = PD_FREQ_MNEMONICS[freq]
    return pd.date_range(start_date, end_date, freq=freq)


def normalize_dates(start_date, end_date, freq):
    """
    Normalize start and end date according to frequency
    by extending the time range.

    So for [1997-11-05, 2020-03-02] and monthly frequency
    this will transform your dates to
    [1997-11-01, 2020-04-01]

    For yearly frequency it will return [1997-01-01, 2021-01-01].

    Args:
        start_date: datetime.date
        end_date: datetime.date
        freq: string with either 'monthly' or 'yearly'.
            Anything else will return the input as is
    Return:
        Tuple of normalized (start_date, end_date)
    """
    if freq in PD_FREQ_MNEMONICS:
        freq = PD_FREQ_MNEMONICS[freq]
    offset = pd.tseries.frequencies.to_offset(freq)
    return (offset.rollback(start_date).date(), offset.rollforward(end_date).date())


def resample_smry_dates(
    eclsumsdates, freq="raw", normalize=True, start_date=None, end_date=None
):
    """
    Resample (optionally) a list of date(time)s to a new datelist according to options.

    Based on the dates as input, a new list at a finer or coarser time density
    can be returned, on the same date range. Incoming dates can also be cropped.

    Args:
        eclsumsdates: list of datetimes, typically coming from EclSum.dates
        freq: string denoting requested frequency for
            the returned list of datetime. 'raw' will
            return the input datetimes (no resampling).
            Options for timeresampling are
            'daily', 'monthly' and 'yearly'.
            'last' will give out the last date (maximum),
            as a list with one element.
        normalize: Whether to normalize backwards at the start
            and forwards at the end to ensure the raw
            date range is covered when resampling time.
        start_date: str or date with first date to include
            Dates prior to this date will be dropped, supplied
            start_date will always be included. Overrides
            normalized dates.
        end_date: str or date with last date to be included.
            Dates past this date will be dropped, supplied
            end_date will always be included. Overrides
            normalized dates. Overriden if freq is 'last'.
    Returns:
        list of datetimes.

    """
    if not eclsumsdates:
        return []

    if start_date:
        if isinstance(start_date, str):
            start_date = dateutil.parser.parse(start_date).date()
        elif isinstance(start_date, datetime.date):
            pass
        else:
            raise TypeError("start_date had unknown type")

    if end_date:
        if isinstance(end_date, str):
            end_date = dateutil.parser.parse(end_date).date()
        elif isinstance(end_date, datetime.date):
            pass
        else:
            raise TypeError("end_date had unknown type")

    if freq == "raw":
        datetimes = eclsumsdates
        datetimes.sort()
        if start_date:
            # Convert to datetime (at 00:00:00)
            start_date = datetime.datetime.combine(
                start_date, datetime.datetime.min.time()
            )
            datetimes = [x for x in datetimes if x > start_date]
            datetimes = [start_date] + datetimes
        if end_date:
            end_date = datetime.datetime.combine(end_date, datetime.datetime.min.time())
            datetimes = [x for x in datetimes if x < end_date]
            datetimes = datetimes + [end_date]
        return datetimes
    if freq == "first":
        return [min(eclsumsdates).date()]
    if freq == "last":
        return [max(eclsumsdates).date()]

    # These are datetime.datetime, not datetime.date
    start_smry = min(eclsumsdates)
    end_smry = max(eclsumsdates)

    (start_n, end_n) = normalize_dates(start_smry.date(), end_smry.date(), freq)

    if not start_date and not normalize:
        start_date_range = start_smry.date()
    elif not start_date and normalize:
        start_date_range = start_n
    else:
        start_date_range = start_date

    if not end_date and not normalize:
        end_date_range = end_smry.date()
    elif not end_date and normalize:
        end_date_range = end_n
    else:
        end_date_range = end_date

    datetimes = date_range(start_date_range, end_date_range, freq)

    # Convert from Pandas' datetime64 to datetime.date:
    datetimes = [x.date() for x in datetimes]

    # pd.date_range will not include random dates that do not
    # fit on frequency boundary. Force include these if
    # supplied as user arguments.
    if start_date and start_date not in datetimes:
        datetimes = [start_date] + datetimes
    if end_date and end_date not in datetimes:
        datetimes = datetimes + [end_date]
    return datetimes


def df(
    eclfiles,
    time_index=None,
    column_keys=None,
    start_date=None,
    end_date=None,
    include_restart=True,
    params=False,
    paramfile=None,
    datetime=False,  # A very poor choice of argument name [pylint]
):
    """
    Extract data from UNSMRY as Pandas dataframes.

    This is a thin wrapper for EclSum.pandas_frame, by adding
    support for string mnenomics for the time index.

    Arguments:
        eclfiles: EclFiles object representing the Eclipse deck. Alternatively
           an EclSum object.
        time_index: string indicating a resampling frequency,
           'yearly', 'monthly', 'daily', 'last' or 'raw', the latter will
           return the simulated report steps (also default).
           If a list of DateTime is supplied, data will be resampled
           to these.
        column_keys: list of column key wildcards. None means everything.
        start_date: str or date with first date to include.
            Dates prior to this date will be dropped, supplied
            start_date will always be included.
        end_date: str or date with last date to be included.
            Dates past this date will be dropped, supplied
            end_date will always be included. Overriden if time_index
            is 'last'.
        include_restart: boolean sent to libecl for wheter restarts
            files should be traversed
        params (bool): If set, parameters.txt will be attempted loaded
            and merged with the summary data.
        paramsfile (str): Explicit path to parameters file if autodiscovery is
            not wanted.
        datetime (bool): If True, the time index of the returned DataFrame
            is always of datetime type. If not, it will be datetime
            if raw dates are requested (which are at second accuracy),
            or it will be strings in case of yearly, monthly or daily
            time frequency.

    Returns empty dataframe if there is no summary file, or if the
    column_keys are not existing.
    """
    if not isinstance(column_keys, list):
        column_keys = [column_keys]
    if isinstance(time_index, str) and time_index == "raw":
        time_index_arg = resample_smry_dates(
            eclfiles.get_eclsum().dates, "raw", False, start_date, end_date
        )
    elif isinstance(time_index, str):
        time_index_arg = resample_smry_dates(
            eclfiles.get_eclsum().dates, time_index, True, start_date, end_date
        )
    else:
        time_index_arg = time_index

    if not column_keys or not column_keys[0]:
        column_keys_str = "*"
    else:
        column_keys_str = ",".join(column_keys)
    logger.info(
        "Requesting columns_keys: %s at time_index: %s",
        column_keys_str,
        str(time_index_arg or "raw"),
    )
    if isinstance(eclfiles, EclSum):
        eclsum = eclfiles
    else:
        eclsum = eclfiles.get_eclsum(include_restart=include_restart)
    dframe = eclsum.pandas_frame(time_index_arg, column_keys)
    # If time_index_arg was None, but start_date was set, we need to date-truncate
    # afterwards:
    logger.info(
        "Dataframe with smry data ready, %d columns and %d rows",
        len(dframe.columns),
        len(dframe),
    )
    dframe.index.name = "DATE"
    if params:
        if not paramfile:
            param_files = parameters.find_parameter_files(eclfiles)
            logger.info("Loading parameters from files: %s", str(param_files))
            param_dict = parameters.load_all(param_files)
        else:
            if not Path(paramfile).is_absolute():
                param_file = parameters.find_parameter_files(
                    eclfiles, filebase=paramfile
                )
                logger.info("Loading parameters from file: %s", str(param_file))
                param_dict = parameters.load(param_file)
            else:
                logger.info("Loading parameter from file: %s", str(paramfile))
                param_dict = parameters.load(paramfile)
        logger.info("Loaded %d parameters", len(param_dict))
        for key in param_dict:
            # By converting to str we are more robust with respect to what objects are
            # read from the parameters.json/txt/yml. Since we are only going
            # to dump to csv, it should not cause side-effects that floats end up
            # as strings in the dataframe.
            dframe[key] = str(param_dict[key])
    if datetime:
        if dframe.index.dtype == "object":
            dframe.index = pd.to_datetime(dframe.index)

    # Add metadata as an attribute the dataframe, using experimental Pandas features:
    meta = smry_meta(eclsum)
    # Slice meta to dataframe columns:
    dframe.attrs["meta"] = {
        column_key: meta[column_key] for column_key in dframe if column_key in meta
    }

    return dframe


def smry_meta(eclfiles):
    """Provide metadata for summary data vectors.

    A dictionary indexed by summary vector name is returned, and each
    value is dictionary with the metadata types provided by the underlying
    EclSum object:

    * unit (string)
    * is_total (bool)
    * is_rate (bool)
    * is_historical (bool)
    * get_num (int) (only provided if not None)
    * keyword (str)
    * wgname (str or None)
    """
    if isinstance(eclfiles, EclSum):
        eclsum = eclfiles
    else:
        eclsum = eclfiles.get_eclsum()
    meta = {}
    for col in eclsum.keys():
        meta[col] = {}
        meta[col]["unit"] = eclsum.unit(col)
        meta[col]["is_total"] = eclsum.is_total(col)
        meta[col]["is_rate"] = eclsum.is_rate(col)
        meta[col]["is_historical"] = eclsum.smspec_node(col).is_historical()
        meta[col]["keyword"] = eclsum.smspec_node(col).keyword
        meta[col]["wgname"] = eclsum.smspec_node(col).wgname
        num = eclsum.smspec_node(col).get_num()
        if num is not None:
            meta[col]["get_num"] = num
    return meta


def _fix_dframe_for_libecl(dframe: pd.DataFrame) -> pd.DataFrame:
    """Fix a dataframe making it ready for EclSum.from_pandas()

    * Ensures that the index is always datetime, and sorted.
    * Removes BLOCK vectors, these are currently not supported as
      it requires knowledge of the grid dimensions. Warnings
      will be emitted for skipped columns

    Args:
        dframe (pd.DataFrame): Dataframe to read. Will not be modified.

    Returns:
        pd.DataFrame: Modified copy of incoming dataframe.
    """
    if dframe.empty:
        return dframe
    dframe = dframe.copy()
    if "DATE" in dframe.columns:
        dframe["DATE"] = pd.to_datetime(dframe["DATE"])
        dframe = dframe.set_index("DATE", drop=True)
    if not isinstance(dframe.index, pd.DatetimeIndex):
        raise ValueError("dataframe must have a DatetimeIndex")
    dframe.sort_index(axis=0, inplace=True)

    # This column will appear if dataframes are naively written to CSV
    # files and read back in again.
    if "Unnamed: 0" in dframe:
        dframe.drop("Unnamed: 0", axis="columns", inplace=True)

    block_columns = [
        col for col in dframe.columns if (col.startswith("B") or col.startswith("LB"))
    ]
    if block_columns:
        dframe = dframe.drop(columns=block_columns)
        logger.warning(
            "Dropped columns with block data, not supported: %s",
            str({colname.partition(":")[0] + ":*" for colname in block_columns}),
        )

    return dframe


def df2eclsum(
    dframe: pd.DataFrame,
    casename: str = "SYNTHETIC",
):
    """Convert a dataframe to an EclSum object

    Args:
        dframe (pd.DataFrame): Dataframe with a DATE colum (or with the
            dates/datetimes in the index).
        casename: Name of Eclipse casename/basename to be used for the EclSum object
            If the EclSum object is later written to disk, this will be used
            to construct the filenames.

    Returns:
        EclSum
    """
    if dframe.empty:
        return None

    if casename.upper() != casename:
        raise ValueError(f"casename {casename} must be UPPER CASE")
    if "." in casename:
        raise ValueError(f"Do not use dots in casename {casename}")

    dframe = _fix_dframe_for_libecl(dframe)
    return EclSum.from_pandas(casename, dframe)


def fill_parser(parser):
    """Set up sys.argv parsers.

    Arguments:
        parser (argparse.ArgumentParser or argparse.subparser): parser to fill
            with arguments
    """
    parser.add_argument(
        "DATAFILE",
        help="Name of Eclipse DATA file. " + "UNSMRY file must lie alongside.",
    )
    parser.add_argument(
        "--time_index",
        type=str,
        help=(
            "Time resolution mnemonic; raw, daily, monthly or yearly. "
            "Data at a given point in time applies until the next point in time. "
            "If not raw, data will be interpolated. Use interpolated rate vectors "
            "with care. Default is raw, which will include clock times. first and last "
            "are also accepted and will print data for the first or the last date. "
        ),
        default="raw",
    )
    parser.add_argument(
        "--column_keys",
        nargs="+",
        help=(
            "Summary column vector wildcards, space-separated. "
            "Default is to include all summary vectors available."
        ),
    )
    parser.add_argument(
        "--start_date",
        type=str,
        help=(
            "Start at a specific date, in ISO format YYYY-MM-DD. "
            "Ignored if time_index is first or last"
        ),
        default="",
    )

    parser.add_argument(
        "--end_date",
        type=str,
        help=(
            "End at a specific date, in ISO format YYYY-MM-DD"
            "Ignored if time_index is first or last"
        ),
        default="",
    )
    parser.add_argument(
        "-p",
        "--params",
        action="store_true",
        help="Merge key-value data from parameter file into each row",
    )
    parser.add_argument(
        "--paramfile",
        type=str,
        help=(
            "Filename of key-value parameter file to look for if -p is set, "
            "relative to Eclipse DATA file or an absolute filename "
            "If not supplied, parameters.{json,yml,txt} in "
            "{., .. and ../..} will be merged in."
        ),
        default=None,
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help=(
            "Name of output csv file. Use '-' to write to stdout. "
            "Default 'summary.csv'"
        ),
        default="summary.csv",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Be verbose")
    return parser


def fill_reverse_parser(parser):
    """Fill a parser for the operation:  dataframe -> eclsum files"""
    parser.add_argument("csvfile", help="Name of CSV file with summary data.")
    parser.add_argument("ECLBASE", help="Basename for Eclipse output files")
    parser.add_argument("-v", "--verbose", action="store_true", help="Be verbose")
    parser.add_argument("--debug", action="store_true", help="Be verbose")
    return parser


def summary_main(args):
    """Read summary data from disk and write CSV back to disk"""
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    eclbase = (
        args.DATAFILE.replace(".DATA", "").replace(".UNSMRY", "").replace(".SMSPEC", "")
    )
    eclfiles = EclFiles(eclbase)
    sum_df = df(
        eclfiles,
        time_index=args.time_index,
        column_keys=args.column_keys,
        start_date=args.start_date,
        end_date=args.end_date,
        params=args.params,
        paramfile=args.paramfile,
    )
    if sum_df.empty:
        logger.warning("Empty summary data being written to disk!")
    write_dframe_stdout_file(sum_df, args.output, logger)


def summary_reverse_main(args):
    """Entry point for usage with "csv2ecl summary" on the command line"""
    if args.verbose and not args.debug:
        logging.basicConfig(level=logging.INFO)
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    summary_df = pd.read_csv(args.csvfile)
    logger.info("Parsed %s", args.csvfile)

    eclsum = df2eclsum(summary_df, args.ECLBASE)
    EclSum.fwrite(eclsum)
    logger.info(
        "Wrote to %s and %s", args.ECLBASE + ".UNSMRY", args.ECLBASE + ".SMSPEC"
    )