# Карта для агентов

- Вход и роли проверяй по `server.py`, `backend/customer.py` и `tests/test_http.py`. Раздельно проверь гостя, клиента, менеджеров и роль жюри.
- Контекст переписки проходит из `web/app.js` в `backend/core.py`. Карточка передаёт ID позиции, корзина передаёт ID и количество. Цену проверяет сервер.
- Маршрутизация ответа начинается в `Store._answer`. Поисковые условия живут в `backend/selection.py`, справка в `backend/knowledge.py`, правила корзины в `backend/basket.py`. Передачу менеджеру проверь через `Store._route` и тесты в `tests/test_core.py`.
- По моделям открой `backend/runtime.py` и `backend/integrations.py`. По стоимости используй `backend/economics.py` и `tests/test_economics.py`.
