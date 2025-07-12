import logging
import typing as t
from collections import namedtuple
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
    PLATFORM_SCHEMA,
)
from homeassistant.const import (
    CONF_NAME,
    CURRENCY_EURO,
    UnitOfEnergy,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import slugify, Throttle

from .api import GreenchoiceApi
from .model import SensorUpdate

_LOGGER = logging.getLogger(__name__)

CONF_USERNAME = "username"
CONF_PASSWORD = "password"  # nosec:B105
CONF_CUSTOMER_NUMBER = "customer_number"
CONF_AGREEMENT_ID = "agreement_id"

DEFAULT_NAME = "Energieverbruik"
DEFAULT_DATE_FORMAT = "%y-%m-%dT%H:%M:%S"

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=3600)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(
            CONF_CUSTOMER_NUMBER,
            description="Fill in if you would like to use a specific customer number",
            default=0,
        ): cv.positive_int,
        vol.Optional(
            CONF_AGREEMENT_ID,
            description="Fill in if you would like to use a specific agreement id",
            default=0,
        ): cv.positive_int,
    }
)


class Unit:
    KWH = UnitOfEnergy.KILO_WATT_HOUR
    EUR_KWH = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"
    M3 = UnitOfVolume.CUBIC_METERS
    EUR_M3 = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"


SensorInfo = namedtuple("SensorInfo", ["device_class", "unit", "icon"])
sensor_infos = {
    "electricity_consumption_off_peak": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "weather-sunset-down"
    ),
    "electricity_consumption_normal": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "weather-sunset-up"
    ),
    "electricity_consumption_total": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "transmission-tower-export"
    ),
    "electricity_feed_in_off_peak": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "solar-power"
    ),
    "electricity_feed_in_normal": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "solar-power"
    ),
    "electricity_return_total": SensorInfo(
        SensorDeviceClass.ENERGY, Unit.KWH, "transmission-tower-import"
    ),
    "electricity_price_single": SensorInfo(
        SensorDeviceClass.MONETARY, Unit.EUR_KWH, "currency-eur"
    ),
    "electricity_price_off_peak": SensorInfo(
        SensorDeviceClass.MONETARY, Unit.EUR_KWH, "currency-eur"
    ),
    "electricity_price_normal": SensorInfo(
        SensorDeviceClass.MONETARY, Unit.EUR_KWH, "currency-eur"
    ),
    "electricity_feed_in_compensation": SensorInfo(
        SensorDeviceClass.MONETARY, Unit.EUR_KWH, "currency-eur"
    ),
    "electricity_feed_in_cost": SensorInfo(
        SensorDeviceClass.MONETARY, Unit.EUR_KWH, "currency-eur"
    ),
    "gas_consumption": SensorInfo(SensorDeviceClass.GAS, Unit.M3, "fire"),
    "gas_price": SensorInfo(SensorDeviceClass.MONETARY, Unit.EUR_M3, "currency-eur"),
}


# noinspection PyUnusedLocal
def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: t.Optional[DiscoveryInfoType] = None,
) -> None:
    name: str = config.get(CONF_NAME)
    username: str = config.get(CONF_USERNAME)
    password: str = config.get(CONF_PASSWORD)
    customer_number: int | None = config.get(CONF_CUSTOMER_NUMBER) or None
    agreement_id: int | None = config.get(CONF_AGREEMENT_ID) or None

    _LOGGER.debug("Set up platform")
    greenchoice_api = GreenchoiceApi(
        username, password, customer_number=customer_number, agreement_id=agreement_id
    )

    throttled_api_update(greenchoice_api)

    sensors = [
        GreenchoiceSensor(
            greenchoice_api,
            name,
            sensor_name,
        )
        for sensor_name in sensor_infos
    ]

    add_entities(sensors, True)


@Throttle(MIN_TIME_BETWEEN_UPDATES)
def throttled_api_update(api) -> SensorUpdate:
    _LOGGER.debug("Throttled update called.")
    api_result = api.update()
    _LOGGER.debug("Api result: %s", api_result)
    return api_result


class GreenchoiceSensor(SensorEntity):
    def __init__(
        self,
        greenchoice_api: GreenchoiceApi,
        name: str,
        measurement_type: str,
    ):
        self._api = greenchoice_api
        self._measurement_type = measurement_type
        self._measurement_date = None
        self._measurement_date_key = (
            "electricity_reading_date"
            if "electricity" in self._measurement_type
            else "gas_reading_date"
        )

        sensor_info = sensor_infos[self._measurement_type]

        self._attr_unique_id = f"{slugify(name)}_{measurement_type}"
        self._attr_name = self._attr_unique_id
        self._attr_icon = f"mdi:{sensor_info.icon}"

        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_device_class = sensor_info.device_class
        self._attr_native_unit_of_measurement = sensor_info.unit

    def update(self):
        """Get the latest data from the Greenchoice API."""
        _LOGGER.debug("Updating %s", self.name)
        api_result = throttled_api_update(self._api) or self._api.result

        if (
            not api_result
            or not hasattr(api_result, self._measurement_type)
            or not hasattr(api_result, self._measurement_date_key)
        ):
            return

        self._attr_native_value = getattr(api_result, self._measurement_type)
        self._measurement_date = getattr(api_result, self._measurement_date_key)

    @property
    def measurement_type(self):
        return self._measurement_type

    @property
    def measurement_date(self):
        return self._measurement_date
