.PHONY: api test frontend install

install:
	pip3 install -r backend/requirements.txt
	cd frontend && npm install

api:
	PYTHONPATH=backend python3 -m uvicorn quantx.api.main:app --host 0.0.0.0 --port 8000

test:
	PYTHONPATH=backend python3 -m pytest tests/ -v

frontend:
	cd frontend && npm run dev

build-frontend:
	cd frontend && npm run build
