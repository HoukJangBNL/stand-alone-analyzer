"""initial v6 schema

Revision ID: 0001_initial_v6
Revises:
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial_v6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ENUM types
    op.execute("CREATE TYPE upload_session_status AS ENUM ('active', 'completed', 'aborted');")
    op.execute("CREATE TYPE upload_item_status AS ENUM ('pending', 'uploading', 'uploaded', 'failed');")
    op.execute("CREATE TYPE pipeline_status AS ENUM ('pending', 'running', 'completed', 'failed');")

    # 1) users
    op.execute("""
        CREATE TABLE users (
            id         BIGSERIAL PRIMARY KEY,
            username   TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("INSERT INTO users (username) VALUES ('system');")

    # 2) models
    op.execute("""
        CREATE TABLE models (
            id          BIGSERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            base_model  TEXT NOT NULL,
            s3_uri      TEXT NOT NULL,
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # 3) scans
    op.execute("""
        CREATE TABLE scans (
            id            BIGSERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            material      TEXT,
            description   TEXT,
            image_count   INT NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id BIGINT REFERENCES users(id)
        );
    """)
    op.execute("CREATE INDEX scans_material_idx ON scans(material) WHERE material IS NOT NULL;")

    # 4) upload_sessions
    op.execute("""
        CREATE TABLE upload_sessions (
            id              BIGSERIAL PRIMARY KEY,
            scan_id         BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            total_files     INT NOT NULL,
            completed_files INT NOT NULL DEFAULT 0,
            failed_files    INT NOT NULL DEFAULT 0,
            status          upload_session_status NOT NULL DEFAULT 'active',
            manifest_s3_uri TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id   BIGINT REFERENCES users(id)
        );
    """)
    op.execute("CREATE INDEX upload_sessions_scan_idx ON upload_sessions(scan_id);")

    # 5) images (must precede upload_items because of FK)
    op.execute("""
        CREATE TABLE images (
            id            BIGSERIAL PRIMARY KEY,
            scan_id       BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            sha256        CHAR(64) NOT NULL,
            s3_uri        TEXT NOT NULL,
            width         INT NOT NULL,
            height        INT NOT NULL,
            filename      TEXT,
            grid_ix       INT,
            grid_iy       INT,
            stage_x_um    REAL,
            stage_y_um    REAL,
            pixel_size_um REAL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(scan_id, sha256)
        );
    """)
    op.execute("CREATE INDEX images_scan_idx ON images(scan_id);")
    op.execute("""
        CREATE INDEX images_grid_idx ON images(scan_id, grid_ix, grid_iy)
            WHERE grid_ix IS NOT NULL AND grid_iy IS NOT NULL;
    """)

    # 6) upload_items
    op.execute("""
        CREATE TABLE upload_items (
            id            BIGSERIAL PRIMARY KEY,
            session_id    BIGINT NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
            sha256        CHAR(64) NOT NULL,
            filename      TEXT NOT NULL,
            size_bytes    BIGINT,
            status        upload_item_status NOT NULL DEFAULT 'pending',
            s3_uri        TEXT,
            error         TEXT,
            attempts      INT NOT NULL DEFAULT 0,
            image_id      BIGINT REFERENCES images(id),
            grid_ix       INT,
            grid_iy       INT,
            stage_x_um    REAL,
            stage_y_um    REAL,
            pixel_size_um REAL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            UNIQUE(session_id, sha256)
        );
    """)
    op.execute("""
        CREATE INDEX upload_items_session_status_idx
            ON upload_items(session_id, status)
            WHERE status IN ('pending', 'uploading');
    """)

    # 7) analyses
    op.execute("""
        CREATE TABLE analyses (
            id                BIGSERIAL PRIMARY KEY,
            scan_id           BIGINT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            model_id          BIGINT NOT NULL REFERENCES models(id),
            name              TEXT,
            amg_params        JSONB NOT NULL,
            background_params JSONB,
            background_s3_uri TEXT,
            link_distance_px  REAL NOT NULL,
            min_area_px       INT NOT NULL DEFAULT 10,
            max_area_px       INT,
            proximity_params  JSONB,
            steps_done        JSONB NOT NULL DEFAULT '{}',
            status            pipeline_status GENERATED ALWAYS AS (
                CASE
                    WHEN steps_done ? 'failed'
                        THEN 'failed'::pipeline_status
                    WHEN steps_done ? 'domain_proximity'
                         AND (steps_done ->> 'domain_proximity')::boolean
                        THEN 'completed'::pipeline_status
                    WHEN jsonb_typeof(steps_done) = 'object'
                         AND steps_done <> '{}'::jsonb
                        THEN 'running'::pipeline_status
                    ELSE 'pending'::pipeline_status
                END
            ) STORED,
            notes             TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id     BIGINT REFERENCES users(id)
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX analyses_scan_model_name_uniq
            ON analyses(scan_id, model_id, name)
            WHERE name IS NOT NULL;
    """)
    op.execute("CREATE INDEX analyses_scan_idx ON analyses(scan_id);")

    # 8) runs
    op.execute("""
        CREATE TABLE runs (
            id            BIGSERIAL PRIMARY KEY,
            analysis_id   BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
            step          TEXT NOT NULL CHECK (step IN (
                              'background',
                              'sam',
                              'domain_stats',
                              'domain_proximity'
                          )),
            status        pipeline_status NOT NULL,
            instance_type TEXT,
            instance_id   TEXT,
            is_spot       BOOLEAN,
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            error         TEXT,
            metrics       JSONB,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("CREATE INDEX runs_analysis_idx ON runs(analysis_id);")
    op.execute("CREATE INDEX runs_analysis_step_idx ON runs(analysis_id, step);")

    # 9) flakes (must precede domains because of FK)
    op.execute("""
        CREATE TABLE flakes (
            id                BIGSERIAL PRIMARY KEY,
            analysis_id       BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
            coordinate_system TEXT NOT NULL DEFAULT 'image_px'
                              CHECK (coordinate_system IN ('image_px', 'stage_um')),
            anchor_image_id   BIGINT REFERENCES images(id),
            n_domains         INT NOT NULL,
            bbox              INT[] NOT NULL,
            area              INT NOT NULL,
            segmentation_rle  JSONB NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("CREATE INDEX flakes_analysis_idx ON flakes(analysis_id);")

    # 10) domains
    op.execute("""
        CREATE TABLE domains (
            id               BIGSERIAL PRIMARY KEY,
            analysis_id      BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
            image_id         BIGINT NOT NULL REFERENCES images(id),
            flake_id         BIGINT REFERENCES flakes(id) ON DELETE SET NULL,
            bbox             INT[] NOT NULL,
            area             INT NOT NULL,
            segmentation_rle JSONB NOT NULL,
            sam_score        REAL,
            stats            JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (analysis_id, id)
        );
    """)
    op.execute("CREATE INDEX domains_analysis_image_idx ON domains(analysis_id, image_id);")
    op.execute("CREATE INDEX domains_image_idx ON domains(image_id);")
    op.execute("CREATE INDEX domains_flake_idx ON domains(flake_id) WHERE flake_id IS NOT NULL;")

    # 11) domain_analyses
    op.execute("""
        CREATE TABLE domain_analyses (
            id                     BIGSERIAL PRIMARY KEY,
            analysis_id            BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
            name                   TEXT NOT NULL,
            selector_params        JSONB NOT NULL DEFAULT '{}',
            selector_params_hash   TEXT,
            n_selected_domains     INT,
            method                 TEXT NOT NULL,
            clustering_params      JSONB NOT NULL,
            clustering_params_hash TEXT,
            model_s3_uri           TEXT,
            status                 pipeline_status NOT NULL DEFAULT 'pending',
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id          BIGINT REFERENCES users(id),
            UNIQUE(analysis_id, name),
            UNIQUE(analysis_id, id)
        );
    """)

    # domain_groups
    op.execute("""
        CREATE TABLE domain_groups (
            id                 BIGSERIAL PRIMARY KEY,
            domain_analysis_id BIGINT NOT NULL REFERENCES domain_analyses(id) ON DELETE CASCADE,
            cluster_id         INT NOT NULL,
            label              TEXT NOT NULL,
            color              TEXT,
            UNIQUE(domain_analysis_id, cluster_id)
        );
    """)

    # domain_assignments
    op.execute("""
        CREATE TABLE domain_assignments (
            analysis_id        BIGINT NOT NULL,
            domain_analysis_id BIGINT NOT NULL,
            domain_id          BIGINT NOT NULL,
            domain_group_id    BIGINT NOT NULL REFERENCES domain_groups(id) ON DELETE CASCADE,
            posterior          REAL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (domain_analysis_id, domain_id),
            FOREIGN KEY (analysis_id, domain_id)
                REFERENCES domains(analysis_id, id) ON DELETE CASCADE,
            FOREIGN KEY (analysis_id, domain_analysis_id)
                REFERENCES domain_analyses(analysis_id, id) ON DELETE CASCADE
        );
    """)
    op.execute("CREATE INDEX domain_assignments_group_idx ON domain_assignments(domain_group_id);")

    # 12) flake_analyses
    op.execute("""
        CREATE TABLE flake_analyses (
            id                 BIGSERIAL PRIMARY KEY,
            analysis_id        BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
            name               TEXT NOT NULL,
            domain_analysis_id BIGINT REFERENCES domain_analyses(id) ON DELETE SET NULL,
            explorer_params    JSONB,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id      BIGINT REFERENCES users(id),
            UNIQUE(analysis_id, name)
        );
    """)

    # flake_curations
    op.execute("""
        CREATE TABLE flake_curations (
            id                BIGSERIAL PRIMARY KEY,
            flake_analysis_id BIGINT NOT NULL REFERENCES flake_analyses(id) ON DELETE CASCADE,
            flake_id          BIGINT NOT NULL REFERENCES flakes(id) ON DELETE CASCADE,
            tag               TEXT,
            is_of_interest    BOOLEAN NOT NULL DEFAULT FALSE,
            notes             TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_id     BIGINT REFERENCES users(id),
            UNIQUE(flake_analysis_id, flake_id)
        );
    """)
    op.execute("CREATE INDEX flake_curations_flake_idx ON flake_curations(flake_id);")


def downgrade() -> None:
    # Drop tables in reverse FK dependency order.
    op.execute("DROP TABLE IF EXISTS flake_curations;")
    op.execute("DROP TABLE IF EXISTS flake_analyses;")
    op.execute("DROP TABLE IF EXISTS domain_assignments;")
    op.execute("DROP TABLE IF EXISTS domain_groups;")
    op.execute("DROP TABLE IF EXISTS domain_analyses;")
    op.execute("DROP TABLE IF EXISTS domains;")
    op.execute("DROP TABLE IF EXISTS flakes;")
    op.execute("DROP TABLE IF EXISTS runs;")
    op.execute("DROP TABLE IF EXISTS analyses;")
    op.execute("DROP TABLE IF EXISTS upload_items;")
    op.execute("DROP TABLE IF EXISTS images;")
    op.execute("DROP TABLE IF EXISTS upload_sessions;")
    op.execute("DROP TABLE IF EXISTS scans;")
    op.execute("DROP TABLE IF EXISTS models;")
    op.execute("DROP TABLE IF EXISTS users;")

    # ENUM types last.
    op.execute("DROP TYPE IF EXISTS pipeline_status;")
    op.execute("DROP TYPE IF EXISTS upload_item_status;")
    op.execute("DROP TYPE IF EXISTS upload_session_status;")
