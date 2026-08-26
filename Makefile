.PHONY: run dev worker docker docker-local docker-scale lint test clean update

run:
	python -m app.main --polling

dev:
	python -m app.main --polling

worker:
	arq app.workers.tasks.WorkerSettings

docker:
	docker compose -f docker/docker-compose.yml up --build

docker-local:
	docker compose --profile local-api -f docker/docker-compose.yml up --build

docker-scale:
	docker compose -f docker/docker-compose.yml up --scale worker=3 --build

docker-down:
	docker compose -f docker/docker-compose.yml down

docker-logs:
	docker compose -f docker/docker-compose.yml logs -f bot worker

lint:
	ruff check app
	mypy app

test:
	pytest -v

clean:
	python -c "from app.core.cleanup import cleanup_sync; print('deleted', cleanup_sync())"

update:
	yt-dlp -U || pip install -U yt-dlp
	python -c "import yt_dlp; print(yt_dlp.version.__version__)"

migrate:
	alembic upgrade head

migrate-create:
	alembic revision --autogenerate -m "$(msg)"
