# Initial TLS certificate hook

The production gateway reads `fullchain.pem` and `privkey.pem` from one dedicated runtime directory. It does not mount Certbot's `live/` symlinks or root-only private-key file directly. The bootstrap Compose file exposes only port 80 and serves only the ACME HTTP-01 challenge directory for the configured host; it does not start the API, database or OAuth services.

1. Create a deployment environment file from `.env.production.example`. Set `SERVER_NAME`, `ACME_WEBROOT_PATH` and `LETSENCRYPT_EMAIL`; leave the OAuth and application values out of the bootstrap command.
2. Create the host ACME webroot and make it readable by container user `101`.
3. Start the temporary challenge gateway:

   ```sh
   docker compose --env-file .env.production -f compose.tls-bootstrap.yaml up -d --wait
   ```

4. From the production host, request the initial certificate with the installed Certbot package:

   ```sh
   certbot certonly --webroot \
     --webroot-path "$ACME_WEBROOT_PATH" \
     --domain "$SERVER_NAME" \
     --email "$LETSENCRYPT_EMAIL" \
     --agree-tos \
     --no-eff-email
   ```

5. Stop the temporary gateway. Set `TLS_RUNTIME_DIRECTORY` to a persistent host directory such as `/opt/alright-mate/shared/tls`, then copy the resolved certificate contents there with group `101`. Copy to temporary names first so Nginx never observes a partially written file.

   ```sh
   docker compose --env-file .env.production -f compose.tls-bootstrap.yaml down
   install -d -o root -g 101 -m 0750 "$TLS_RUNTIME_DIRECTORY"
   install -o root -g 101 -m 0640 "/etc/letsencrypt/live/$SERVER_NAME/fullchain.pem" "$TLS_RUNTIME_DIRECTORY/.fullchain.pem.new"
   install -o root -g 101 -m 0640 "/etc/letsencrypt/live/$SERVER_NAME/privkey.pem" "$TLS_RUNTIME_DIRECTORY/.privkey.pem.new"
   mv -f "$TLS_RUNTIME_DIRECTORY/.privkey.pem.new" "$TLS_RUNTIME_DIRECTORY/privkey.pem"
   mv -f "$TLS_RUNTIME_DIRECTORY/.fullchain.pem.new" "$TLS_RUNTIME_DIRECTORY/fullchain.pem"
   docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml up -d --wait
   ```

6. Configure the host's normal Certbot renewal timer. Its root-owned deploy hook must repeat the four `install`/`mv` commands above, then validate and reload the already-running gateway:

   ```sh
   docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml exec -T gateway nginx -t
   docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml exec -T gateway nginx -s reload
   ```

The Docker runtime test mounts a temporary directory, starts Nginx with an initial certificate, replaces both files, validates the configuration and reloads Nginx. It never invokes Certbot or a real certificate authority. DNS, firewall rules, a registered GitHub OAuth application and real certificate issuance remain operator-controlled production steps.
