PY := .venv/bin/python
CUR = $(shell $(PY) -m pipeline.config --current)

.PHONY: setup data history texts summaries site dev all validate

setup:            ## create venv + install deps
	uv venv .venv && uv pip install --python $(PY) -r requirements.txt
	cd site && npm install

data:             ## refresh the current legislature's open data + roster, new attendance records, normalize votes (same as the daily CI job)
	$(PY) -m pipeline.fetch_opendata --refresh $(CUR)
	$(PY) -m pipeline.fetch_deputies --refresh $(CUR)
	$(PY) -m pipeline.normalize
	$(PY) -m pipeline.fetch_attendance
	$(PY) -m pipeline.fetch_photos

history:          ## re-scrape closed history into data/scraped/ (XIV 1st session, all attendance); slow, rarely needed
	$(PY) -m pipeline.fetch_xiv_session1
	$(PY) -m pipeline.fetch_attendance --all

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
