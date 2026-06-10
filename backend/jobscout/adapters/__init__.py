"""JobScout adapter registry.

Import all concrete adapters here so that the rest of the codebase (ingestion
scheduler, tests, CLI) can do::

    from jobscout.adapters import AdzunaAdapter, JobSpyAdapter
"""

from jobscout.adapters.adzuna import AdzunaAdapter
from jobscout.adapters.arbeitnow import ArbeitnowAdapter
from jobscout.adapters.ashby import AshbyAdapter
from jobscout.adapters.greenhouse import GreenhouseAdapter
from jobscout.adapters.jobicy import JobicyAdapter
from jobscout.adapters.jobrightai import JobrightAIAdapter
from jobscout.adapters.jobspy_adapter import JobSpyAdapter
from jobscout.adapters.lever import LeverAdapter
from jobscout.adapters.recruitee import RecruiteeAdapter
from jobscout.adapters.remoteok import RemoteOKAdapter
from jobscout.adapters.remotive import RemotiveAdapter
from jobscout.adapters.rippling import RipplingAdapter
from jobscout.adapters.rss import RssAdapter
from jobscout.adapters.smartrecruiters import SmartRecruitersAdapter
from jobscout.adapters.themuse import TheMuseAdapter
from jobscout.adapters.workable import WorkableAdapter
from jobscout.adapters.workday import WorkdayAdapter
from jobscout.adapters.workingnomads import WorkingNomadsAdapter

__all__ = [
    "AdzunaAdapter",
    "ArbeitnowAdapter",
    "AshbyAdapter",
    "GreenhouseAdapter",
    "JobicyAdapter",
    "JobrightAIAdapter",
    "JobSpyAdapter",
    "LeverAdapter",
    "RecruiteeAdapter",
    "RemoteOKAdapter",
    "RemotiveAdapter",
    "RipplingAdapter",
    "RssAdapter",
    "SmartRecruitersAdapter",
    "TheMuseAdapter",
    "WorkableAdapter",
    "WorkdayAdapter",
    "WorkingNomadsAdapter",
]
