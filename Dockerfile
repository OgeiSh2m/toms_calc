FROM python:3.9-slim

RUN apt-get update && apt-get install make

RUN mkdir /app

COPY . /app

WORKDIR /app

EXPOSE 8000

CMD ["make", "run"]
