.PHONY: up down ps logs nuke fixtures verify-fixtures replay-audit \
        chaos-worker-kill chaos-redis-stop chaos-rabbit-stop chaos-dispatcher-kill chaos-disk-fill

PG = docker compose exec -T postgres sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB -tAc "$(1)"'

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f -t

nuke:
	docker compose down -v --remove-orphans

fixtures:
	docker compose run --rm --no-deps -e FORCE celery python scripts/make_fixtures.py

verify-fixtures:
	docker compose run --rm --no-deps celery python scripts/verify_fixtures.py

# Replay the whole log through the AUDIT group only.
replay-audit:
	@echo "rows before: $$($(call PG,select count(*) from events;))"
	docker compose stop audit
	docker compose exec -T kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
		--reset-offsets --to-earliest --group audit --topic media-jobs --execute
	docker compose start audit
	@sleep 8
	@echo "rows after:  $$($(call PG,select count(*) from events;))"
	@echo "(equal => replay re-consumed the full log and inserted zero duplicates)"

chaos-worker-kill:
	bash scripts/chaos/worker_kill.sh
chaos-redis-stop:
	bash scripts/chaos/redis_stop.sh
chaos-rabbit-stop:
	bash scripts/chaos/rabbit_stop.sh
chaos-dispatcher-kill:
	bash scripts/chaos/dispatcher_kill.sh
chaos-disk-fill:
	bash scripts/chaos/disk_fill.sh $(ARGS)
