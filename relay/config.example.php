<?php
// Install OUTSIDE every public document root: /home/negaau5/fancontrol-private/config.php
// Directory mode 0700, files 0600. Never put this file or the .p8 key in FanControl/.
return [
    'enabled' => false,
    'team_id' => '',
    'key_id' => '',
    'key_file' => '/home/negaau5/fancontrol-private/AuthKey.p8',
    'topic' => '', // Exact signed watch app bundle identifier.
    'installations' => [
        // 'installation-id' => ['secret' => 'separate random secret of at least 32 bytes'],
    ],
];
