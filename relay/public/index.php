<?php
declare(strict_types=1);
// This service has no HA URL, HA credential, or vehicle command capability.
ini_set('display_errors', '0');
header('Content-Type: application/json');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
function reply(int $status, array $body): never {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    exit;
}
function b64(string $s): string { return rtrim(strtr(base64_encode($s), '+/', '-_'), '='); }
function jwt(array $config): string {
    $text = b64(json_encode(['alg'=>'ES256','kid'=>$config['key_id']], JSON_THROW_ON_ERROR)).'.'.
        b64(json_encode(['iss'=>$config['team_id'],'iat'=>time()], JSON_THROW_ON_ERROR));
    $key = openssl_pkey_get_private(file_get_contents($config['key_file']));
    $details = $key ? openssl_pkey_get_details($key) : false;
    if (!$details || ($details['ec']['curve_name'] ?? '') !== 'prime256v1' ||
        !openssl_sign($text, $der, $key, OPENSSL_ALGO_SHA256)) {
        throw new RuntimeException('Signing unavailable');
    }
    // OpenSSL returns DER ECDSA integers; JWT requires fixed-width R || S.
    $offset = 2;
    if (ord($der[0]) !== 0x30 || ord($der[1]) >= 128) { throw new RuntimeException('Invalid signature'); }
    $raw = '';
    for ($i = 0; $i < 2; $i++) {
        if (ord($der[$offset++]) !== 2) { throw new RuntimeException('Invalid integer'); }
        $length = ord($der[$offset++]);
        $value = ltrim(substr($der, $offset, $length), "\0");
        $offset += $length;
        if (strlen($value) > 32) { throw new RuntimeException('Invalid width'); }
        $raw .= str_pad($value, 32, "\0", STR_PAD_LEFT);
    }
    return $text.'.'.b64($raw);
}
try {
    if (($_SERVER['HTTPS'] ?? '') !== 'on') { reply(403, ['error'=>'https_required']); }
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        reply(200, ['service'=>'FanControl push relay','protocol'=>1,'status'=>'available','authentication'=>'required']);
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        header('Allow: GET, POST'); reply(405, ['error'=>'method_not_allowed']);
    }
    // On this host the public entry is /home/negaau5/coffeybean.com/FanControl/index.php.
    $private = dirname(__DIR__, 2).'/fancontrol-private';
    if (!is_file($private.'/config.php')) { reply(503, ['error'=>'not_configured']); }
    $config = require $private.'/config.php';
    if (!($config['enabled'] ?? false)) { reply(503, ['error'=>'not_configured']); }
    $raw = file_get_contents('php://input', false, null, 0, 4097);
    if (strlen($raw) > 4096) { reply(413, ['error'=>'request_too_large']); }
    if (!str_starts_with(strtolower($_SERVER['CONTENT_TYPE'] ?? ''), 'application/json')) {
        reply(415, ['error'=>'json_required']);
    }
    $installation = $_SERVER['HTTP_X_FANCONTROL_INSTALLATION'] ?? '';
    $timestamp = $_SERVER['HTTP_X_FANCONTROL_TIMESTAMP'] ?? '';
    $nonce = $_SERVER['HTTP_X_FANCONTROL_NONCE'] ?? '';
    $signature = $_SERVER['HTTP_X_FANCONTROL_SIGNATURE'] ?? '';
    $secret = $config['installations'][$installation]['secret'] ?? '';
    if (!preg_match('/^[A-Za-z0-9_-]{1,64}$/D', $installation) || strlen($secret) < 32 ||
        !preg_match('/^[0-9]{10}$/D', $timestamp) || abs(time() - (int)$timestamp) > 300 ||
        !preg_match('/^[a-f0-9]{32}$/D', $nonce) ||
        !hash_equals(hash_hmac('sha256', $timestamp."\n".$nonce."\n".$raw, $secret), $signature)) {
        reply(401, ['error'=>'unauthorized']);
    }
    try { $body = json_decode($raw, true, 16, JSON_THROW_ON_ERROR); }
    catch (JsonException) { reply(400, ['error'=>'invalid_json']); }
    if (!is_array($body)) { reply(400, ['error'=>'invalid_request']); }
    $action = $body['action'] ?? '';
    $watch = $body['watch_id'] ?? '';
    if (!is_string($watch) || !preg_match('/^[A-Za-z0-9._-]{1,128}$/D', $watch) ||
        !in_array($action, ['register','unregister','refresh'], true)) { reply(400, ['error'=>'invalid_request']); }
    $allowed = match ($action) {
        'register' => ['action','watch_id','device_token','environment'],
        'unregister' => ['action','watch_id'],
        'refresh' => ['action','watch_id','reason'],
    };
    if (array_diff(array_keys($body), $allowed)) { reply(400, ['error'=>'unknown_fields']); }
    if ($action === 'register' && (!is_string($body['device_token'] ?? null) ||
        !preg_match('/^[a-fA-F0-9]{64,200}$/D', $body['device_token']) ||
        !in_array($body['environment'] ?? '', ['sandbox','production'], true))) {
        reply(400, ['error'=>'invalid_registration']);
    }
    if ($action === 'refresh' && !in_array($body['reason'] ?? '', ['selection','configuration','active_panel_state'], true)) {
        reply(400, ['error'=>'invalid_reason']);
    }
    umask(0077);
    $db = new PDO('sqlite:'.$private.'/relay.sqlite', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
    $db->exec('PRAGMA busy_timeout=3000');
    $db->exec('CREATE TABLE IF NOT EXISTS nonces (installation TEXT, nonce TEXT, created INTEGER, PRIMARY KEY(installation,nonce))');
    $db->exec('CREATE TABLE IF NOT EXISTS watches (installation TEXT, watch TEXT, token TEXT, environment TEXT, PRIMARY KEY(installation,watch))');
    $db->exec('DELETE FROM nonces WHERE created < '.(time()-600));
    try {
        $db->prepare('INSERT INTO nonces VALUES (?,?,?)')->execute([$installation,$nonce,time()]);
    } catch (PDOException $e) {
        if ($e->getCode() === '23000') { reply(409, ['error'=>'replayed_request']); }
        throw $e;
    }
    // Bound authenticated traffic per installation to protect APNs and host resources.
    $count = $db->prepare('SELECT COUNT(*) FROM nonces WHERE installation=? AND created>=?');
    $count->execute([$installation,time()-60]);
    if ((int)$count->fetchColumn() > 60) { header('Retry-After: 60'); reply(429, ['error'=>'rate_limited']); }
    if ($action === 'register') {
        $db->prepare('INSERT OR REPLACE INTO watches VALUES (?,?,?,?)')->execute([$installation,$watch,strtolower($body['device_token']),$body['environment']]);
        reply(200, ['status'=>'registered']);
    }
    if ($action === 'unregister') {
        $db->prepare('DELETE FROM watches WHERE installation=? AND watch=?')->execute([$installation,$watch]);
        reply(200, ['status'=>'unregistered']);
    }
    $query = $db->prepare('SELECT token, environment FROM watches WHERE installation=? AND watch=?');
    $query->execute([$installation,$watch]);
    $registration = $query->fetch(PDO::FETCH_ASSOC);
    if (!$registration) { reply(404, ['error'=>'watch_not_registered']); }
    if (!function_exists('curl_init') || !(curl_version()['features'] & CURL_VERSION_HTTP2)) {
        reply(503, ['error'=>'http2_unavailable']);
    }
    // Reuse a provider token to avoid APNs TooManyProviderTokenUpdates errors.
    $lock = fopen($private.'/provider.lock', 'c+');
    if (!$lock || !flock($lock, LOCK_EX)) { throw new RuntimeException('Lock unavailable'); }
    $cached = stream_get_contents($lock);
    $cached = $cached ? json_decode($cached, true) : null;
    $fingerprint = hash('sha256', $config['key_id'].'|'.$config['team_id'].'|'.hash_file('sha256', $config['key_file']));
    if (!is_array($cached) || ($cached['fingerprint'] ?? '') !== $fingerprint || ($cached['created'] ?? 0) < time()-3000) {
        $cached = ['fingerprint'=>$fingerprint,'created'=>time(),'jwt'=>jwt($config)];
        rewind($lock); ftruncate($lock, 0); fwrite($lock, json_encode($cached, JSON_THROW_ON_ERROR)); fflush($lock);
    }
    flock($lock, LOCK_UN); fclose($lock);
    $host = $registration['environment'] === 'production' ? 'api.push.apple.com' : 'api.sandbox.push.apple.com';
    $curl = curl_init('https://'.$host.'/3/device/'.$registration['token']);
    curl_setopt_array($curl, [CURLOPT_POST=>true,CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_HTTP_VERSION=>CURL_HTTP_VERSION_2_0,CURLOPT_CONNECTTIMEOUT=>5,CURLOPT_TIMEOUT=>12,
        CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS,
        CURLOPT_HTTPHEADER=>['authorization: bearer '.$cached['jwt'],'apns-topic: '.$config['topic'],
            'apns-push-type: background','apns-priority: 5','apns-expiration: '.(time()+300),
            'apns-collapse-id: fancontrol-refresh','content-type: application/json'],
        CURLOPT_POSTFIELDS=>' {"aps":{"content-available":1},"fancontrol":{"refresh":true}}']);
    $result = curl_exec($curl);
    $status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    curl_close($curl);
    if ($result === false) { reply(502, ['error'=>'push_delivery_unknown']); }
    if ($status === 200) { reply(202, ['status'=>'accepted_by_apns','watch_delivery_confirmed'=>false]); }
    $reason = json_decode($result, true)['reason'] ?? '';
    if ($status === 410 || ($status === 400 && $reason === 'BadDeviceToken')) {
        $db->prepare('DELETE FROM watches WHERE installation=? AND watch=? AND token=? AND environment=?')->execute([$installation,$watch,$registration['token'],$registration['environment']]);
        reply(410, ['error'=>'registration_expired']);
    }
    reply(502, ['error'=>'push_rejected']);
} catch (Throwable) {
    // Never disclose keys, tokens, request bodies or internal paths.
    reply(503, ['error'=>'service_unavailable']);
}
