.PHONY: up down logs test ps

# Start the whole stack: backend on :8000, frontend on :3000
up:
	@test -f .env || { echo "Missing .env - run: cp .env.example .env  (then add at least 2 real API keys)"; exit 1; }
	docker compose up --build -d
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: http://localhost:3000"

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

# Backend tests (no real API keys needed)
test:
	cd backend && pip install -q -r requirements-dev.txt && python -m pytest -q
