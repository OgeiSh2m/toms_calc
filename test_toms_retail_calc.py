#!/usr/bin/env python3

import http.client
import json
import unittest
import urllib.parse
import urllib.request
from contextlib import contextmanager
from decimal import ROUND_UP, Decimal
from http import HTTPStatus
from threading import Thread
from typing import Any, Union

from toms_retail_calc import (
    TomsRetailCalcHTTPHandler,
    calculate_discount_price,
    calculate_final_price,
    calculate_tax_price,
    make_http_server,
)


class TestCalculateDiscountPrice(unittest.TestCase):
    def setUp(self):
        discounts = {
            "1000": "3",
            "5000": "5",
            "7000": "7",
            "10000": "10",
            "50000": "15",
        }
        self.discounts = {
            Decimal(key): Decimal(value) for key, value in discounts.items()
        }

    def test_calculate_discount_price(self):
        param_list = [
            (0, 0),
            (900, 900),
            (999, 999),
            (1000, Decimal("970")),
            (2500, Decimal("2425")),
            (5000, Decimal("4750")),
            (6001, Decimal("5700.95")),
            (7001, Decimal("6510.93")),
            (Decimal(10000.25), Decimal("9000.23")),
            (50000, Decimal("42500")),
            (60000, Decimal("51000")),
        ]
        for amount, expected_price_with_discount in param_list:
            with self.subTest():
                price_with_discount = calculate_discount_price(self.discounts, amount)
                price_with_discount = price_with_discount.quantize(
                    Decimal(".01"), ROUND_UP
                )
                self.assertEqual(price_with_discount, expected_price_with_discount)


class TestCalculateTax(unittest.TestCase):
    def setUp(self):
        taxes = {
            "UT": "6.85",
            "NV": "8",
            "TX": "6.25",
            "AL": "4",
            "CA": "8.25",
        }
        self.taxes = {key: Decimal(value) for key, value in taxes.items()}

    def test_calculate_tax(self):
        param_list = [
            ("UT", 10000, Decimal("10685.00")),
            ("UT", 123, Decimal("131.43")),
            ("NV", 0, 0),
            ("TX", Decimal(0.1), Decimal("0.11")),
            ("AL", 5991, Decimal("6230.64")),
            ("CA", Decimal(99.3), Decimal("107.50")),
        ]
        for state_code, amount, expected_tax_price in param_list:
            with self.subTest():
                price_with_tax = calculate_tax_price(self.taxes, state_code, amount)
                price_with_tax = price_with_tax.quantize(Decimal(".01"), ROUND_UP)
                self.assertEqual(price_with_tax, expected_tax_price)

    def test_calculate_tax_unknown_state(self):
        with self.assertRaises(KeyError):
            calculate_tax_price(self.taxes, "UNKNOWN", Decimal(100))


class TestCalculateFinalPrice(unittest.TestCase):
    def setUp(self):

        discounts = {
            "1000": "3",
            "5000": "5",
            "7000": "7",
            "10000": "10",
            "50000": "15",
        }
        self.discounts = {
            Decimal(key): Decimal(value) for key, value in discounts.items()
        }

        taxes = {
            "UT": "6.85",
            "NV": "8",
            "TX": "6.25",
            "AL": "4",
            "CA": "8.25",
        }
        self.taxes = {key: Decimal(value) for key, value in taxes.items()}

    def test_calculate_final_price(self):
        param_list = [
            (100, 1000, "AL", 50_000),
            (0, 1000, "AL", 0),
            (1, 999, "NV", 0),
            (1, 1000, "NV", 1000),
            (100, 400, "AL", 10_000),
        ]
        for product_amount, single_product_price, state_code, discount in param_list:
            with self.subTest():
                final_price = calculate_final_price(
                    self.discounts,
                    self.taxes,
                    product_amount=product_amount,
                    single_product_price=single_product_price,
                    state_code=state_code,
                )
                expected_final_price = self.calculate_expected_final_price(
                    discount=discount,
                    state_code=state_code,
                    product_amount=product_amount,
                    single_product_price=single_product_price,
                )
                self.assertEqual(final_price, expected_final_price)

    def calculate_expected_final_price(
        self, discount, state_code, product_amount, single_product_price
    ):

        discount = self.discounts.get(discount, 0)
        tax = self.taxes[state_code]
        total_price = product_amount * single_product_price

        expected_price_with_discount = total_price * (Decimal(100 - discount) / 100)

        expected_final_price = expected_price_with_discount * (Decimal(100 + tax) / 100)

        return expected_price_with_discount, expected_final_price


class TestHTTPApi(unittest.TestCase):
    def setUp(self):
        self.host = "localhost"
        self.port = 8888
        self.base_url = f"http://{self.host}:{self.port}"
        self.calc_url = "/api/v1/calc/final_and_discount_price"

    def test_ok(self):
        with self.start_server():

            price_with_discount, final_price = self.calculate_final_price(
                product_amount=100, single_product_price=3000, state_code="AL"
            )

            expected_price_with_discount = 255000
            expected_final_price = 265200

            self.assertEqual(price_with_discount, expected_price_with_discount)
            self.assertEqual(final_price, expected_final_price)

    def test_missing_required_params(self):
        with self.start_server():
            with self.get({"product_amount": 1, "single_product_price": 33}) as resp:

                self.assertEqual(resp.getcode(), HTTPStatus.UNPROCESSABLE_ENTITY)

                json_resp = json.load(resp)
                self.assertTrue(json_resp["error"] is not None)

    def test_product_amount_validation(self):
        param_list = [-1, -100, "not int"]
        with self.start_server():
            for product_amount in param_list:
                with self.subTest():
                    with self.get(
                        {
                            "product_amount": product_amount,
                            "single_product_price": 33,
                            "state_code": "TX",
                        }
                    ) as resp:

                        self.assertEqual(
                            resp.getcode(), HTTPStatus.UNPROCESSABLE_ENTITY
                        )

                        json_resp = json.load(resp)
                        self.assertTrue(json_resp["error"] is not None)

    def test_single_product_price_validation(self):
        param_list = [-1, -100, "not int"]
        with self.start_server():
            for single_product_price in param_list:
                with self.subTest():
                    with self.get(
                        {
                            "product_amount": 100,
                            "single_product_price": single_product_price,
                            "state_code": "TX",
                        }
                    ) as resp:

                        self.assertEqual(
                            resp.getcode(), HTTPStatus.UNPROCESSABLE_ENTITY
                        )

                        json_resp = json.load(resp)
                        self.assertTrue(json_resp["error"] is not None)

    def test_state_validation(self):
        with self.start_server():
            with self.get(
                {
                    "product_amount": 1,
                    "single_product_price": 33,
                    "state_code": "NOT SUPPORTED",
                }
            ) as resp:

                self.assertEqual(resp.getcode(), HTTPStatus.UNPROCESSABLE_ENTITY)

                json_resp = json.load(resp)
                self.assertTrue(json_resp["error"] is not None)

    def calculate_final_price(
        self,
        product_amount: int,
        single_product_price: Union[Decimal, int],
        state_code: str,
    ):
        with self.get(
            {
                "product_amount": product_amount,
                "single_product_price": str(single_product_price),
                "state_code": state_code,
            }
        ) as resp:
            json_resp = json.load(resp)

            self.assertTrue(json_resp["error"] is None)
            self.assertEqual(resp.getcode(), HTTPStatus.OK)
            data = json_resp["data"]
            return data["price_with_discount"], data["final_price"]

    @contextmanager
    def start_server(self):
        with make_http_server(
            HandlerClass=TomsRetailCalcHTTPHandler, port=self.port
        ) as httpd:

            t = Thread(target=httpd.serve_forever)
            t.start()

            try:
                yield
            finally:
                httpd.shutdown()
                t.join()

    @contextmanager
    def get(self, params: dict[str, Any]):
        qs = urllib.parse.urlencode(params)
        url = f"{self.calc_url}?{qs}"

        conn = http.client.HTTPConnection(self.host, self.port)
        try:
            conn.request("GET", url)
            resp = conn.getresponse()
            yield resp
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
