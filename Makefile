run:
	./toms_retail_calc.py

docker_run:
	@docker build -t toms_calc .
	@docker run -it --rm -p 8000:8000 toms_calc:latest

test:
	./test_toms_retail_calc.py

docker_test:
	@docker build -t toms_calc .
	@docker run -it --rm toms_calc:latest make test

.PHONY: run test docker_run docker_test
