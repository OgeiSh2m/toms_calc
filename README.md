## Запуск

### Сервер

Сервер запускается на :: port 8000.
Для запуска сервера можно использовать `make`:

(потребуется `make` и `python 3.9`)

``` sh
make run
```

или (должен быть установлен docker)

``` sh
make docker run
```

Web UI будет доступен по адресу:
http://127.0.0.1:8000

Можно тестировать через `curl`:

``` sh
curl -s 'http://127.0.0.1:8000/api/v1/calc/final_and_discount_price?product_amount=400&single_product_price=400&state_code=AL'  | jq
```


### Тесты

[![Python application](https://github.com/OgeiSh2m/toms_calc/actions/workflows/tests.yaml/badge.svg)](https://github.com/OgeiSh2m/toms_calc/actions/workflows/tests.yaml)

Тесы запускаются через `make`:

(потребуется `make` и `python 3.9`)

``` sh
make test
```

или (должен быть установлен docker)

``` sh
make docker_test
```


## Про код

При выполнении задания мне захотелось отойти от привычного для меня стека и обойтись только стандартной библиотекой
`python 3.9`

Для простоты весь код собран в одном файле, я постарался разделить его на секции комментариями, чтобы 
его было удобно читать в свернутом (folded) виде.
