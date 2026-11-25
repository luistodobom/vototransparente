PY := .venv/bin/python

.PHONY: setup data texts summaries site dev all validate

setup:            ## create venv + install deps
	uv venv .venv && uv pip install --python $(PY) -r requirements.txt
	cd site && npm install

data:             ## download official open data (initiatives + MP roster) and normalize votes
	$(PY) -m pipeline.fetch_opendata --force
	$(PY) -m pipeline.fetch_deputies --force
	$(PY) -m pipeline.fetch_attendance
	$(PY) -m pipeline.fetch_photos
	$(PY) -m pipeline.fetch_xiv_session1
	$(PY) -m pipeline.normalize

texts:            ## download the full text of every voted initiative
	$(PY) -m pipeline.fetch_texts

summaries:        ## generate AI summaries for initiatives without one (needs GEMINI_API_KEY)
	$(PY) -m pipeline.summarize

site:             ## attribute MP votes, export data and build the static site (commit site/src/data + site/public/data, then push to deploy)
	$(PY) -m pipeline.mp_votes
	$(PY) -m pipeline.provenance
	$(PY) -m pipeline.export_site
	cd site && npm run build

dev:              ## run the site in dev mode (search needs a prior `make site`)
	cd site && npm run dev

validate:         ## compare votes against the old DB and maisliberdade.pt
	$(PY) validation/validate_votes.py

all: data texts summaries site
