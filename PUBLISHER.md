# Mathesis publisher operations

The generated catalog is described in [README.md](README.md). Publisher source,
controls and validation are documented in [docs/PUBLISHER.md](docs/PUBLISHER.md).
`publisher.json` pins source commits and selects automatic public archive eligibility.
`publish.json` retains the reviewed baseline; `repositories.json` is the durable
mapping of published papers. Changing these controls requires review.

The hourly/manual pipeline preserves exact originals and versions, publishes paper
pages independently of CMA, then checks their public files. [FACTORY.md](FACTORY.md)
and [factory.json](factory.json) list actual reading/formalization follow-up; workflow
artifacts retain publication and live readback receipts. Unchanged papers receive
no new commit.
