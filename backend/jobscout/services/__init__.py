"""Service layer — business logic extracted from the API routers.

Routers (api/routes) stay thin and delegate to these stateless functions, which
take the open stores as parameters. Layering: routers → services → repositories
(store.py / relational.py) → schemas (models.py).
"""
