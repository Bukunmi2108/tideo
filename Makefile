.PHONY: up down ps logs nuke fixtures verify-fixtures

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f -t

nuke:
	docker compose down -v

fixtures:
	docker compose run --rm --no-deps -e FORCE celery python scripts/make_fixtures.py

verify-fixtures:
	docker compose run --rm --no-deps celery python scripts/verify_fixtures.py