"""
ApexSpreadator — Broker Factory
Dynamically instantiates the requested broker with automated port assignment.
"""
import sys
from core.broker_interface import BrokerBase
from core.ibkr_broker import IBKRBroker
from core.moomoo_broker import MoomooBroker


def get_broker(broker_name: str, config) -> BrokerBase:
    """
    Broker Factory to get the requested broker instance.
    Automatically switches port based on broker and live mode flag.
    """
    name_clean = broker_name.strip().lower()
    is_live = "--live" in sys.argv

    # Resolve automated port assignment
    if name_clean == "ibkr":
        port = 7496 if is_live else 7497
        return IBKRBroker(
            host=config.connection.host,
            port=port,
            client_id=config.connection.client_id
        )
    elif name_clean == "moomoo":
        port = 11111
        return MoomooBroker(
            host=config.connection.host,
            port=port,
            client_id=config.connection.client_id
        )
    else:
        raise ValueError(f"Unknown broker type: '{broker_name}'. Supported brokers are 'ibkr' and 'moomoo'.")
