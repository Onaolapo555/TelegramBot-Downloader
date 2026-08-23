.PHONY: run dev worker docker lint test clean update

run:
	python -m app.main --polling

dev:
	python -m app.main --polling

worker:
	arq app.workers.tasks.WorkerSettings

docker:
	docker compose -f docker/docker-compose.yml up --build

docker-down:
	docker compose -f docker/docker-compose.yml down

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
