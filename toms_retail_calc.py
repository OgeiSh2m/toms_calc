#!/usr/bin/env python3

import inspect
import json
import logging
import shutil
import socket
import sys
import urllib.parse
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from functools import wraps
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Optional,
    Protocol,
    TypeVar,
    get_args,
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Types

DISCOUNTS = dict[Decimal, Decimal]
STATE_CODE = str
TAXES = dict[STATE_CODE, Decimal]


# Public calculating API


def calculate_discount_price(discounts: DISCOUNTS, price: Decimal) -> Decimal:
    discount_from_amount = _calculate_discount(discounts, price)
    return price - discount_from_amount


def calculate_tax_price(
    taxes: TAXES, state_code: STATE_CODE, price: Decimal
) -> Decimal:
    tax = _calculate_tax(taxes, state_code, price)
    return price + tax


def calculate_final_price(
    discounts: DISCOUNTS,
    taxes: TAXES,
    product_amount: int,
    single_product_price: Decimal,
    state_code: STATE_CODE,
    how_to_round=ROUND_UP,
) -> tuple[Decimal, Decimal]:

    total_product_price = _calculate_total_product_price(
        product_amount, single_product_price
    )

    price_with_discount_from_amount = calculate_discount_price(
        discounts, total_product_price
    )

    price_with_tax = calculate_tax_price(
        taxes, state_code, price_with_discount_from_amount
    )

    final_price = price_with_tax

    return (
        _round_to_cents(price_with_discount_from_amount, how_to_round),
        _round_to_cents(final_price, how_to_round),
    )


# APP


class AppError(Exception):
    "Base app exception"


class ValidationError(AppError):
    "Validation error"


@dataclass
class Config:
    discounts: DISCOUNTS
    taxes: TAXES
    how_to_round: Literal["ROUND_UP", "ROUND_DOWN"] = "ROUND_UP"
    index_html: Path = Path("./index.html")


class App:
    def __init__(self):
        self.config = self._load_config()

    def _load_config(self) -> Config:
        discounts = {
            "1000": "3",
            "5000": "5",
            "7000": "7",
            "10000": "10",
            "50000": "15",
        }

        taxes = {
            "UT": "6.85",
            "NV": "8",
            "TX": "6.25",
            "AL": "4",
            "CA": "8.25",
        }

        return Config(
            discounts={
                Decimal(key): Decimal(value) for key, value in discounts.items()
            },
            taxes={key: Decimal(value) for key, value in taxes.items()},
        )

    def calculate_final_price(
        self,
        product_amount: int,
        single_product_price: Decimal,
        state_code: STATE_CODE,
    ) -> tuple[Decimal, Decimal]:

        logger.info(
            "calculate_final_price: product_amount=%s, single_product_price=%s, state_code=%s",
            product_amount,
            single_product_price,
            state_code,
        )

        self._validate(product_amount, single_product_price, state_code)

        final_price = calculate_final_price(
            self.config.discounts,
            self.config.taxes,
            product_amount=product_amount,
            single_product_price=single_product_price,
            state_code=state_code,
            how_to_round=self.config.how_to_round,
        )

        logger.info("calculate_final_price: result=%s", final_price)

        return final_price

    def _validate(
        self,
        product_amount: int,
        single_product_price: Decimal,
        state_code: STATE_CODE,
    ):
        self._validate_product_amount(product_amount)
        self._validate_single_product_price(single_product_price)
        self._validate_state_code(state_code)

    def _validate_product_amount(self, product_amount: int):
        if product_amount < 0:
            err = ValidationError("product_amount must be >= 0")
            logger.error("product amount validation error: %s", err)
            raise err

    def _validate_single_product_price(self, single_product_price: Decimal):
        if single_product_price < 0:
            err = ValidationError("single_product_price must be >= 0")
            logger.error("single product price validation error: %s", err)
            raise err

    def _validate_state_code(self, state_code: STATE_CODE):
        if state_code not in self.config.taxes:
            supported_state_codes = ", ".join(self.config.taxes)
            err = ValidationError(
                f"Unknown state code '{state_code}': must be one of {supported_state_codes}"
            )
            logger.error("state code validation error: %s", err)
            raise err


# HTTP API


class HTTPError(Exception):
    "Base HTTP Error"


class HTTPValidationError(HTTPError):
    "Validation error for HTTP API"


class HTTPNotFoundError(HTTPError):
    "Route does not exist"

    def __init__(self, path: str):
        self.path = path

    def __str__(self):
        return f"404: not found ({self.path} does not exist)"


@dataclass
class Handler:
    """HTTP handler with argument validation/convertation based on function
    signature.
    """

    handler: Callable[..., Any]

    def __post_init__(self):
        signature = inspect.signature(self.handler)
        self.params = signature.parameters

    @property
    def optional_params(self):
        return {
            param_name: param
            for param_name, param in self.params.items()
            if _is_optional(param)
        }

    @property
    def required_params(self):
        return {
            param_name: param
            for param_name, param in self.params.items()
            if not _is_optional(param)
        }


@dataclass
class RawHandler:
    """HTTP handler without any additional logic.
    User must explicitly manage HTTP headers.
    """

    handler: Callable[..., Any]


class NotSoBaseHTTPRequestHandler(BaseHTTPRequestHandler):
    """Extended http request handler with path based routing and json responses."""

    def __init__(self, *args, **kwargs):
        self.parsed_url = None
        self.request_params = {}

        if not hasattr(self, "routes"):
            self.routes = {}

        self.default_handler = Handler(handler=self.not_found_handler)

        logger = logging.getLogger("toms_retail_calc:http")
        super().__init__(*args, **kwargs)

    def json_responder(self, fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)

            return self.send_json_success_response(result)

        return wrapper

    def file_content_responder(self, path: Path):
        try:
            with open(path) as f:
                fs = path.stat()

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-type", "text/html")
                self.send_header("Content-Length", str(fs[6]))
                self.end_headers()

                with open(path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
        except FileNotFoundError:
            return self.not_found_handler()

    def not_found_handler(self, *args, **kwargs):
        return self.send_json_error_response(
            HTTPNotFoundError(self.path), status_code=HTTPStatus.NOT_FOUND
        )

    def do_GET(self):
        """Serve a GET request."""

        try:
            self.parsed_url = urllib.parse.urlsplit(self.path)
            self.request_params = dict(urllib.parse.parse_qsl(self.parsed_url.query))

            handler = self._dispatch_on_path()

            if isinstance(handler, Handler):

                self._check_required_params(handler)
                params = self._convert_parameters(handler)

                handler.handler(**params)

            elif isinstance(handler, RawHandler):
                handler.handler()

        except (ValidationError, HTTPValidationError) as exc:
            logger.error("HTTP validation error: %s", exc)
            self.send_json_error_response(
                exc, status_code=HTTPStatus.UNPROCESSABLE_ENTITY
            )

        except Exception as exc:
            logger.exception("Unhandled exception: %s", exc)
            self.send_json_error_response(exc)

    def send_json_error_response(
        self,
        exc: Exception,
        message: Optional[str] = None,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
    ):
        response = {
            "data": None,
            "error": {
                "error": repr(exc),
                "message": message if message is not None else str(exc),
            },
        }
        return self._send_json_response(response, status_code=status_code)

    def send_json_success_response(
        self,
        obj: Any,
        status_code=HTTPStatus.OK,
    ):
        error = {
            "data": obj,
            "error": None,
        }
        return self._send_json_response(error, status_code=status_code)

    def _send_json_response(self, obj: Any, status_code=HTTPStatus.OK):
        response = json.dumps(obj).encode("utf8")

        self.send_response(status_code)
        self.send_header("Connection", "close")

        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()

        self.wfile.write(response)

    def _dispatch_on_path(self):
        if self.parsed_url is None:
            return self.default_handler
        return self.routes.get(self.parsed_url.path, self.default_handler)

    def _convert_parameters(self, handler: Handler):
        handler_params = handler.params
        result = {}
        for key, value in self.request_params.items():
            if key in handler_params:
                convert = handler_params[key].annotation
                try:
                    if callable(convert):
                        value = convert(value)
                except Exception as exc:
                    raise HTTPValidationError(
                        f"Could not convert '{key}' using {convert}: {exc}"
                    )
            result[key] = value
        return result

    def _check_required_params(self, handler: Handler):
        for param in handler.required_params:
            if param not in self.request_params:
                raise HTTPValidationError(f"Missing required parameter: '{param}'")


class TomsRetailCalcHTTPHandler(NotSoBaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.app = App()

        self.routes = {
            "/": RawHandler(handler=self.serve_index_html),
            "/api/v1/calc/final_and_discount_price": Handler(
                handler=self.json_responder(self.calculate_final_price),
            ),
        }

        super().__init__(*args, **kwargs)

    def calculate_final_price(
        self,
        product_amount: int,
        single_product_price: Decimal,
        state_code: STATE_CODE,
    ):
        price_with_discount, final_price = self.app.calculate_final_price(
            product_amount=product_amount,
            single_product_price=single_product_price,
            state_code=state_code,
        )
        return {
            "price_with_discount": float(price_with_discount),
            "final_price": float(final_price),
        }

    def serve_index_html(self):
        return self.file_content_responder(self.app.config.index_html)


def make_http_server(
    HandlerClass=BaseHTTPRequestHandler,
    ServerClass=ThreadingHTTPServer,
    protocol="HTTP/1.0",
    port=8000,
    bind=None,
):
    def _get_best_family(*address):
        infos = socket.getaddrinfo(
            *address,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
        family, type, proto, canonname, sockaddr = next(iter(infos))
        return family, sockaddr

    ServerClass.address_family, addr = _get_best_family(bind, port)

    HandlerClass.protocol_version = protocol
    return ServerClass(addr, HandlerClass)


def run_http_server(
    HandlerClass=TomsRetailCalcHTTPHandler,
    port=8000,
):
    with make_http_server(HandlerClass=HandlerClass, port=port) as httpd:
        host, port = httpd.socket.getsockname()[:2]
        url_host = f"[{host}]" if ":" in host else host
        logger.info(
            f"Serving HTTP on {host} port {port} " f"(http://{url_host}:{port}/) ..."
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("\nKeyboard interrupt received, exiting.")
            sys.exit(0)


# HELPERS


def _calculate_total_product_price(
    product_amount: int,
    single_product_price: Decimal,
) -> Decimal:
    return product_amount * single_product_price


def _calculate_discount(discounts: DISCOUNTS, amount: Decimal) -> Decimal:
    discount_percent = _find_discount_by_amount(discounts, amount)
    return _percent_from_value(amount, discount_percent)


def _calculate_tax(taxes: TAXES, state_code: STATE_CODE, amount: Decimal) -> Decimal:
    tax = taxes[state_code]
    return _percent_from_value(amount, tax)


def _round_to_cents(amount: Decimal, how_to_round=ROUND_UP):
    return amount.quantize(Decimal(".01"), how_to_round)


def _find_discount_by_amount(discounts: DISCOUNTS, amount: Decimal) -> Decimal:
    closest_discount_amount = _find_closest_in_range(discounts.keys(), amount)

    if closest_discount_amount is None:
        return Decimal(0)

    return discounts[closest_discount_amount]


def _percent_from_value(value: Decimal, percent: Optional[Decimal]) -> Decimal:
    if percent is None:
        return Decimal(0)
    return Decimal(value) * Decimal(percent) / Decimal(100)


def _is_optional(param: inspect.Parameter) -> bool:
    args_or_kwargs = (param.VAR_KEYWORD, param.VAR_POSITIONAL)
    return param.kind in args_or_kwargs or type(None) in get_args(param.annotation)


class Comparable(Protocol):
    def __lt__(self, other: Any) -> bool:
        ...

    def __gt__(self, other: Any) -> bool:
        ...


CT = TypeVar("CT", bound=Comparable)


def _find_closest_in_range(ranges: Iterable[CT], what_to_find: CT) -> Optional[CT]:
    """Find closest range that includes item.

    Items in `ranges` and `what_to_find` must be comparable.

    Example:

    Inputs:

      ranges = [355, 100, 4600]
      what_to_find = 256

    Visualized:

      0-----100--------355------------4600------->
                 256

    For given inputs, `_find_closest_in_range` will return 100, because
    100 is the closes range that includes 256.

    """

    ranges = sorted(ranges)

    while ranges:

        middle_item_index = len(ranges) // 2
        middle_item = ranges[middle_item_index]

        if what_to_find == middle_item:
            return what_to_find

        elif what_to_find > middle_item:
            if len(ranges) == 1:
                return middle_item

            ranges = ranges[middle_item_index:]

        elif what_to_find < middle_item:

            if ranges[middle_item_index - 1] < what_to_find:
                return ranges[middle_item_index - 1]

            ranges = ranges[:middle_item_index]


# Logging


def setup_logging():
    logger = logging.getLogger()
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# Main


def main():
    setup_logging()
    run_http_server()


if __name__ == "__main__":
    main()
