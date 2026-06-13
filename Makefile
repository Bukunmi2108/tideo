.PHONY: up down ps laogs nuke

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