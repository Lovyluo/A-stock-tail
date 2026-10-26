"""Closed A-D worker selection; all preparation and IO use the existing worker."""
from . import minute_protocol_differential_contract as profiles
from .minute_protocol_worker import main

if __name__ == "__main__":
    raise SystemExit(main(contract_profile=profiles))
