# Coolify: готовые образы из GHCR

GitHub Actions собирает приложение, Coolify скачивает и запускает образы.
На сервере не нужны Node.js, Python и сборка исходников.

## 1. Первая сборка в GitHub

1. В форке откройте **Actions** и разрешите запуск workflows, если GitHub
   показывает кнопку включения Actions для форка.
2. После слияния изменений в `main` дождитесь успешного задания `build` в
   **Build and Publish Docker Images**. Последующие push в `main` запускают его
   автоматически; повторный запуск доступен через **Run workflow** на `main`.
3. Проверьте появление двух пакетов:
   - `ghcr.io/kirei4ik/pingcrm/backend`
   - `ghcr.io/kirei4ik/pingcrm/frontend`

Workflow использует `GITHUB_TOKEN`: `contents: read`, `packages: write` только
у задания сборки. Отдельный PAT, `SERVER_HOST` и `SSH_PRIVATE_KEY` для публикации
не нужны. Политика организации/репозитория должна разрешать Actions и запись
пакетов. При `permission_denied: write_package` проверьте **Manage Actions
access** существующего пакета: этот форк должен иметь право Write.

В pull request образы собираются без входа в GHCR и без публикации.
Публикация выполняется при push в `main`, тега `v*` или ручном запуске.
`latest` обновляется на `main` и при публикации тега; каждый запуск публикации
также создаёт `sha-<полный SHA коммита>`. Теги версий создаются из semver-тегов.
Ручной запуск другой ветки создаёт SHA-тег, но не обновляет `latest`.

Сборки используют `backend/Dockerfile` с контекстом `backend/` и
`frontend/Dockerfile` с контекстом `frontend/`, отдельные кеши GitHub Actions
и платформу **linux/amd64**. ARM-серверу потребуется добавить сборку arm64.
Worker использует тот же backend-образ. WhatsApp sidecar не входит в этот
Coolify Compose и этим workflow не собирается.

## 2. Доступ Coolify к образам

Новые GHCR-пакеты по умолчанию приватные, даже у публичного репозитория.
Выберите один вариант для обоих пакетов:

- Сделайте пакеты публичными в **Package settings → Change visibility**:
  тогда Coolify скачивает их без авторизации.
- Оставьте приватными: создайте PAT (classic) с `read:packages` у пользователя
  с доступом к пакетам. В Coolify откройте **Servers → ваш сервер → Registries**
  (либо **Registries** в боковой панели), выполните **Log in** в `ghcr.io`
  с GitHub-логином и этим токеном. Для старых версий Coolify выполните
  `docker login ghcr.io` по SSH от того же пользователя сервера, которого
  использует Coolify. Не добавляйте токен в Compose или Git.

Доступ к Git-репозиторию и доступ к GHCR настраиваются отдельно.
Авторизуйте каждый сервер, который будет скачивать приватные образы.

## 3. Настройка приложения в Coolify

1. Используйте приложение из Git-репозитория `kirei4ik/pingcrm`, ветку `main`
   и способ сборки **Docker Compose**. Укажите файл
   `/docker-compose.coolify.yml` (путь относительно корня репозитория).
   Для существующего приложения сохраните прежние тома и идентификатор ресурса.
2. В переменных окружения задайте:

   | Переменная | Значение |
   | --- | --- |
   | `PINGCRM_URL` | Публичный адрес, например `https://crm.example.com`, без `/api` и завершающего `/` |
   | `POSTGRES_PASSWORD` | Пароль PostgreSQL |
   | `REDIS_PASSWORD` | Пароль Redis |
   | `SECRET_KEY` | Случайный секрет приложения |
   | `ENCRYPTION_KEY` | Ключ Fernet для шифрования интеграций |
   | `PINGCRM_IMAGE_TAG` | `latest` или `sha-<полный SHA успешной сборки>` |

   API-ключи и OAuth-параметры задавайте при подключении интеграций.
   Для паролей БД/Redis используйте случайные hex-строки, чтобы специальные
   символы не нарушили URL подключения. На существующей установке сохраните
   прежние пароли, `SECRET_KEY` и `ENCRYPTION_KEY`.
3. Маршруты обслуживает встроенный прокси Coolify; отдельного Caddy/gateway
   в Compose нет. Перезагрузите Compose из Git в настройках приложения.
   Если ранее домен был назначен сервису `gateway`, удалите старую привязку.
   В **Domains → Add domain** добавьте маршруты для одного публичного домена:

   | Service | Protocol | Domain (пример) | Internal Port | Path |
   | --- | --- | --- | --- | --- |
   | `frontend` | HTTPS | `crm.example.com` | `3000` | `/` |
   | `backend` | HTTPS | `crm.example.com` | `8000` | `/api` |
   | `backend` | HTTPS | `crm.example.com` | `8000` | `/mcp` |
   | `backend` | HTTPS | `crm.example.com` | `8000` | `/ws` |
   | `backend` | HTTPS | `crm.example.com` | `8000` | `/static` |

   Если интерфейс использует одно поле URL, для frontend введите
   `https://crm.example.com:3000`, для backend —
   `https://crm.example.com:8000/api`, `https://crm.example.com:8000/mcp`,
   `https://crm.example.com:8000/ws`, `https://crm.example.com:8000/static`.
   Порты в этих значениях выбирают порт контейнера; посетители используют
   обычный HTTPS-порт 443.

   **Обязательно:** в **Advanced → Proxy → Path prefixes** выберите
   **Keep paths as-is** (в старом интерфейсе отключите **Strip Prefixes**).
   Backend ожидает исходные пути, например `/api/v1/contacts`, а не
   `/v1/contacts`. Более конкретные маршруты backend имеют приоритет над
   корневым маршрутом frontend. Не публикуйте порты через `ports:`;
   PostgreSQL, Redis и worker остаются доступны только внутри стека.
4. Дождитесь публикации **обоих** образов и нажмите **Deploy / Redeploy**.
   Все сервисы используют `image:`, локальных `build:` в этом файле нет.
   `pull_policy: always` заставляет Compose проверить образы при развёртывании.
5. Проверьте `https://ваш-домен/api/health`, страницу входа и логи backend,
   worker и прокси Coolify. Backend выполняет миграции перед запуском API;
   worker ожидает успешной проверки здоровья backend.

## Адрес API во frontend

При сборке передаётся `NEXT_PUBLIC_API_URL=http://backend:8000`.
В этом проекте переменная используется в `frontend/next.config.ts` для
внутренних rewrites; конфигурация сама добавляет `/api/:path*`.
Браузер обращается к `/api/...` на текущем публичном домене через прокси Coolify.
Не передавайте `${PINGCRM_URL}/api`: получится лишний сегмент `/api`.
Не добавляйте переменную обратно в runtime environment frontend — она
фиксируется при `next build`. Домен приложения задаётся через `PINGCRM_URL`;
`FRONTEND_URL` передаётся и backend, и worker через общий блок окружения.
Изменение маршрутов и удаление Caddy не требуют пересборки образов; можно
использовать уже опубликованные образы той же версии.

## Обновление и откат

Отключите немедленный автодеплой Coolify по push: webhook GitHub может
прийти раньше завершения сборки и скачать предыдущий `latest`.
После успешного задания `build` запускайте Redeploy вручную.
Для Service Stack используйте **Pull Latest Images & Restart**, если доступно.

Для согласованного обновления frontend/backend задайте `PINGCRM_IMAGE_TAG`
равным `sha-<40 символов SHA>` из успешного запуска `main` и выполните Redeploy.
Один тег применяется ко всем трём сервисам приложения. `latest` может
временно указывать на разные сборки, если публикация одного образа завершилась
ошибкой. Для отката укажите SHA предыдущей успешной сборки; учтите, что откат
образов не отменяет миграции базы данных. Перед обновлением сделайте её резервную копию.

Справка: [GitHub GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[публикация из Actions](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images),
[реестры Coolify](https://coolify.io/docs/applications/builds/docker-registries),
[домены Coolify](https://coolify.io/docs/core/networking/domains).
