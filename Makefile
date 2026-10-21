.PHONY: install test test-pg demo up lint build clean
install:      ## dev install with every extra
	pip install -e ".[server,client,dev]"
test:         ## full suite on SQLite (GUI tests run offscreen)
	python -m pytest -q
test-pg:      ## full suite on PostgreSQL:  make test-pg PGURL=postgresql+psycopg://user:pw@localhost/smartprint_test
	SMARTPRINT_TEST_DB=$(PGURL) python -m pytest -q
demo:         ## narrated, self-checking tour of every promise on the slides
	smartprint-demo
up:           ## demo shop on :8000 (then run smartprint-kiosk / smartprint-staff)
	smartprint-demo up
build:        ## PyInstaller executables into dist/ (build on the target OS)
	python packaging/build.py
clean:
	rm -rf build dist data .pytest_cache
