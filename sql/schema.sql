-- ============================================
-- Game Schedule Rotation System DB Schema
-- MySQL 8.0+
-- ============================================

CREATE DATABASE IF NOT EXISTS game_schedule
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE game_schedule;

-- --------------------------------------------
-- 1. rounds
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS rounds (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    gm_ts       INT          NOT NULL COMMENT 'betman round unique id',
    gm_id       VARCHAR(10)  NOT NULL DEFAULT 'G011',
    round_number VARCHAR(20) NOT NULL COMMENT 'round display number (gmOsidTs)',
    ym  VARCHAR(6)   NOT NULL COMMENT 'YYYYMM',
    status      ENUM('upcoming','open','closed') NOT NULL DEFAULT 'open' COMMENT 'upcoming, open or closed',
    result_saved TINYINT(1)  NOT NULL DEFAULT 0 COMMENT 'result save completed flag',
    sale_start  DATETIME     NULL COMMENT 'sale start datetime',
    sale_end    DATETIME     NULL COMMENT 'sale end datetime',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_gm_ts (gm_ts)
) ENGINE=InnoDB COMMENT='round info';

-- --------------------------------------------
-- 2. win result codes (Betman raw result code master)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS win_result_codes (
    code         SMALLINT     NOT NULL PRIMARY KEY COMMENT 'Betman raw result code',
    value        VARCHAR(20)  NOT NULL COMMENT 'Korean label from API',
    pick_result  ENUM('W','D','L') NULL COMMENT 'normalized result for picks, NULL for special/cancel',
    sort_order   SMALLINT     NOT NULL DEFAULT 0 COMMENT 'display order',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='Betman win result code master';

INSERT INTO win_result_codes (code, value, pick_result, sort_order) VALUES
    (0,  '승',       'W', 1),
    (1,  '무',       'D', 2),
    (2,  '패',       'L', 3),
    (3,  '적중특례', NULL, 4),
    (99, '취소',     NULL, 5)
ON DUPLICATE KEY UPDATE
    value = VALUES(value),
    pick_result = VALUES(pick_result),
    sort_order = VALUES(sort_order),
    updated_at = CURRENT_TIMESTAMP;

-- --------------------------------------------
-- 3. games (14 games per round)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS games (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    round_id    INT          NOT NULL,
    game_no     TINYINT      NOT NULL COMMENT 'game number 1-14',
    league      VARCHAR(100) NOT NULL COMMENT 'league name',
    home_team   VARCHAR(100) NOT NULL COMMENT 'home team',
    away_team   VARCHAR(100) NOT NULL COMMENT 'away team',
    game_date   VARCHAR(50)  NULL COMMENT 'game datetime string from API',
    home_score  SMALLINT     NULL COMMENT 'home score from result API',
    away_score  SMALLINT     NULL COMMENT 'away score from result API',
    win_result_code SMALLINT NULL COMMENT 'raw result code from Betman result API',
    result      ENUM('W','D','L') NULL COMMENT 'actual result (W/D/L, home-based)',
    result_checked_at DATETIME NULL COMMENT 'latest result sync time',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_round_game (round_id, game_no),
    KEY idx_games_win_result_code (win_result_code),
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    FOREIGN KEY (win_result_code) REFERENCES win_result_codes(code)
) ENGINE=InnoDB COMMENT='game info';

-- --------------------------------------------
-- 4. users
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(50)  NOT NULL COMMENT 'login ID',
    display_name VARCHAR(50)  NOT NULL COMMENT 'display name',
    password     VARCHAR(255) NOT NULL COMMENT 'password hash',
    sort_order   TINYINT      NOT NULL DEFAULT 0 COMMENT 'rotation cycle order 0,1,2...',
    is_active    TINYINT(1)   NOT NULL DEFAULT 1,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB COMMENT='user info';

INSERT INTO users (username, display_name, password, sort_order) VALUES
    ('kwang', '광', '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', 0),
    ('bum',   '범', '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', 1),
    ('cho',   '초', '$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi', 2)
ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), sort_order = VALUES(sort_order);

-- --------------------------------------------
-- 5. rotation base config (DB managed)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS rotation_base_config (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    base_round_number  INT     NOT NULL COMMENT 'base round number (e.g. 20)',
    rotation_no        TINYINT NOT NULL COMMENT 'rotation number 1,2,3',
    user_id            INT     NOT NULL COMMENT 'assigned user at base round',
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_base_rotation (base_round_number, rotation_no),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='rotation base config';

-- Round 20: rot1=cho(3), rot2=kwang(1), rot3=bum(2)
INSERT INTO rotation_base_config (base_round_number, rotation_no, user_id) VALUES
    (20, 1, 3),
    (20, 2, 1),
    (20, 3, 2)
ON DUPLICATE KEY UPDATE user_id = VALUES(user_id);

-- --------------------------------------------
-- 6. rotation assignments (per round)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS rotation_assignments (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    round_id    INT      NOT NULL,
    user_id     INT      NOT NULL,
    rotation_no TINYINT  NOT NULL COMMENT 'rotation number 1,2,3',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_round_user (round_id, user_id),
    UNIQUE KEY uk_round_rotation (round_id, rotation_no),
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='round rotation assignments';

-- rotation game mapping (fixed)
-- rot1: game 2,3,12,13
-- rot2: game 4,5,10,11
-- rot3: game 6,7,8,9
-- game 1,14: excluded
CREATE TABLE IF NOT EXISTS rotation_games (
    rotation_no TINYINT NOT NULL COMMENT 'rotation number 1,2,3',
    game_no     TINYINT NOT NULL COMMENT 'game number',
    PRIMARY KEY (rotation_no, game_no)
) ENGINE=InnoDB COMMENT='rotation game mapping';

INSERT INTO rotation_games (rotation_no, game_no) VALUES
    (1, 2), (1, 3), (1, 12), (1, 13),
    (2, 4), (2, 5), (2, 10), (2, 11),
    (3, 6), (3, 7), (3, 8),  (3, 9)
ON DUPLICATE KEY UPDATE game_no = VALUES(game_no);

-- --------------------------------------------
-- 7. picks (user picks + correct flag)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS picks (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    round_id    INT      NOT NULL,
    game_id     INT      NOT NULL,
    user_id     INT      NOT NULL,
    pick        ENUM('W','D','L') NOT NULL COMMENT 'W/D/L home-based',
    is_correct  TINYINT(1) NULL COMMENT '1=correct, 0=wrong, NULL=pending',
    picked_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_round_game_user (round_id, game_id, user_id),
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='user picks';

-- --------------------------------------------
-- 8. round user results (summary)
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS round_user_results (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    round_id    INT     NOT NULL,
    user_id     INT     NOT NULL,
    total_picks INT     NOT NULL DEFAULT 0 COMMENT 'total pick count',
    correct     INT     NOT NULL DEFAULT 0 COMMENT 'correct count',
    wrong       INT     NOT NULL DEFAULT 0 COMMENT 'wrong count',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_round_user (round_id, user_id),
    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='round user result summary';
