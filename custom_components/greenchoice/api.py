import logging
from datetime import datetime, UTC
from typing import Union

import requests

from .auth import Auth
from .model import MeterReadings, Reading, Rates, Profile, SensorUpdate
from .model import Preferences
from .util import curl_dump

# Force the log level for easy debugging.
# None          - Don't force any log level and use the defaults.
# logging.DEBUG - Force debug logging.
#   See the logging package for additional log levels.
_FORCE_LOG_LEVEL: Union[int, None] = None
_LOGGER = logging.getLogger(__name__)
if _FORCE_LOG_LEVEL is not None:
    _LOGGER.setLevel(_FORCE_LOG_LEVEL)

BASE_URL = "https://mijn.greenchoice.nl"


class ApiError(Exception):
    def __init__(self, message: str):
        _LOGGER.error(message)
        super().__init__(message)


class GreenchoiceApi:
    def __init__(
        self,
        username: str,
        password: str,
        customer_number: int | None = None,
        agreement_id: int | None = None,
    ):
        self.auth = Auth(BASE_URL, username, password)
        self.customer_number: int | None = customer_number
        self.agreement_id: int | None = agreement_id

        self.result: SensorUpdate = SensorUpdate()

    def _authenticated_request(
        self, method: str, endpoint: str, data=None, json=None
    ) -> requests.models.Response:
        _LOGGER.debug(
            f"Request: {method} {endpoint} {data if data is not None else json}"
        )
        response = self.auth.session.request(method, endpoint, data=data, json=json)
        if self.auth.is_session_expired(response):
            self.session = self.auth.refresh_session()
            response = self.auth.session.request(method, endpoint, data=data, json=json)

        _LOGGER.debug(curl_dump(response.request))

        return response

    def request(self, endpoint: str, data=None, _retry_count=2) -> requests.Response:
        try:
            target_url = BASE_URL + endpoint
            response = self._authenticated_request("GET", target_url, json=data)

            if len(response.history) > 1:
                _LOGGER.debug("Response history len > 1. %s", response.history)

            # Some api's may not work and there might be fallbacks for them
            if response.status_code == 404:
                return response

            response.raise_for_status()
        except requests.HTTPError as e:
            _LOGGER.error("HTTP Error: %s", e)
            _LOGGER.error("Cookies: %s", [c.name for c in self.session.cookies])
            if _retry_count == 0:
                raise ApiError(f"HTTP Error: {e}")

            _LOGGER.debug("Retrying request")
            return self.request(endpoint, data, _retry_count - 1)

        _LOGGER.debug("Request success")
        return response

    @staticmethod
    def _validate_response(response: requests.Response) -> dict:
        if not response:
            raise ApiError("Error retrieving response!")

        try:
            response_json = response.json()
        except requests.exceptions.JSONDecodeError as e:
            raise ApiError(f"Could not parse response: invalid JSON: {e}")

        return response_json

    def get_preferences(self) -> Preferences:
        preferences_json = self._validate_response(self.request("/api/v2/Preferences/"))
        return Preferences(**preferences_json)

    def get_profiles(self) -> list[Profile]:
        profiles_json = self._validate_response(self.request("/api/v2/Profiles/"))
        return [Profile(**p) for p in profiles_json]

    def get_meter_readings(self) -> MeterReadings:
        meter_json = self._validate_response(
            self.request(
                MeterReadings.Request(
                    customer_number=self.customer_number,
                    agreement_id=self.agreement_id,
                    year=datetime.now(UTC).year,
                ).build_url(),
            )
        )

        # noinspection PyTypeChecker
        return MeterReadings(product_types=meter_json)

    def get_rates(self) -> Rates:
        pricing_details = self._validate_response(
            self.request(
                Rates.Request(
                    customer_number=self.customer_number,
                    agreement_id=self.agreement_id,
                ).build_url(),
            )
        )

        return Rates(**pricing_details)

    def update(self) -> SensorUpdate:
        self.result = SensorUpdate()
        if not self.customer_number or not self.agreement_id:
            try:
                preferences = self.get_preferences()
                self.customer_number = preferences.subject.customer_number
                self.agreement_id = preferences.subject.agreement_id
            except ApiError:
                _LOGGER.error("Cant get preferences")
                return self.result

        try:
            self.update_usage_values(self.result)
        except ApiError:
            _LOGGER.error("Cant update usage values")
            pass

        try:
            self.update_contract_values(self.result)
        except ApiError:
            _LOGGER.error("Cant update contract values")
            pass

        return self.result

    def update_usage_values(self, result: SensorUpdate) -> None:
        _LOGGER.debug("Retrieving meter values")

        meter_readings = self.get_meter_readings()

        electricity_reading: Reading | None = meter_readings.last_electricity_reading
        gas_reading: Reading | None = meter_readings.last_gas_reading

        if electricity_reading:
            result.electricity_consumption_off_peak = (
                electricity_reading.off_peak_consumption
            )
            result.electricity_consumption_normal = (
                electricity_reading.normal_consumption
            )

            result.electricity_consumption_total = (
                electricity_reading.off_peak_consumption
                + electricity_reading.normal_consumption
            )
            result.electricity_feed_in_off_peak = electricity_reading.off_peak_feed_in
            result.electricity_feed_in_normal = electricity_reading.normal_feed_in
            result.electricity_feed_in_total = (
                electricity_reading.off_peak_feed_in
                + electricity_reading.normal_feed_in
            )
            result.electricity_reading_date = electricity_reading.reading_date

        if gas_reading:
            result.gas_consumption = gas_reading.gas
            result.gas_reading_date = gas_reading.reading_date

    def update_contract_values(self, result: SensorUpdate) -> None:
        _LOGGER.debug("Retrieving contract values")

        pricing_details = self.get_rates()

        if pricing_details.electricity:
            electricity_usage = (
                pricing_details.electricity.rates.usage_dependent_electricity_rates
            )

            result.electricity_price_single = (
                electricity_usage.all_in_delivery_single_including_vat
            )
            result.electricity_price_off_peak = (
                electricity_usage.all_in_delivery_low_including_vat
            )
            result.electricity_price_normal = (
                electricity_usage.all_in_delivery_normal_including_vat
            )
            result.electricity_feed_in_compensation = (
                electricity_usage.feed_in_compensation
            )
            result.electricity_feed_in_cost = (
                electricity_usage.feed_in_cost_including_vat
            )

        if pricing_details.gas:
            result.gas_price = pricing_details.gas.rates.usage_dependent_gas_rates.all_in_delivery_including_vat
