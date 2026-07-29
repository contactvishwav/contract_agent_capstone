#!/usr/bin/env python3
"""
Migration runner script.
Usage: python run_migration.py [command]
Commands: upgrade, downgrade, sample, migrate_contracts, rollback,
          migrate_all (all 8 schema migrations, versioned/idempotent -
          see backend/migrations/run_all_migrations.py), status
"""

import sys
import os
import logging
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent
sys.path.insert(0, str(backend_path))

from migrations.multi_level_embeddings import upgrade_schema, downgrade_schema, create_sample_data
from embeddings.migrator import EmbeddingMigrator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

def main():
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py [upgrade|downgrade|sample|migrate_contracts|migrate_all|status]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    try:
        if command == "upgrade":
            logger.info("Running schema upgrade...")
            upgrade_schema()
            logger.info("Schema upgrade completed successfully!")
            
        elif command == "downgrade":
            logger.info("Running schema downgrade...")
            downgrade_schema()
            logger.info("Schema downgrade completed successfully!")
            
        elif command == "sample":
            logger.info("Creating sample data...")
            create_sample_data()
            logger.info("Sample data created successfully!")
            
        elif command == "migrate_contracts":
            logger.info("Migrating existing contracts to multi-level embeddings...")
            migrator = EmbeddingMigrator()
            
            # Get batch size from command line or use default
            batch_size = 5
            if len(sys.argv) > 2:
                try:
                    batch_size = int(sys.argv[2])
                except ValueError:
                    logger.warning(f"Invalid batch size '{sys.argv[2]}', using default: {batch_size}")
            
            stats = migrator.migrate_existing_contracts(batch_size=batch_size)
            
            logger.info("Migration completed!")
            logger.info(f"Total contracts: {stats['total_contracts']}")
            logger.info(f"Successful: {stats['successful']}")
            logger.info(f"Failed: {stats['failed']}")
            
            if stats['errors']:
                logger.error("Errors encountered:")
                for error in stats['errors']:
                    logger.error(f"  - {error}")
        
        elif command == "rollback":
            logger.info("Rolling back contract migrations...")
            migrator = EmbeddingMigrator()
            migrator.rollback_migration()
            logger.info("Rollback completed!")

        elif command == "migrate_all":
            from migrations.run_all_migrations import MigrationRunner
            logger.info("Running all pending schema migrations (versioned, idempotent)...")
            result = MigrationRunner().run_pending()
            logger.info(f"Applied: {result['applied']}")
            logger.info(f"Already applied (skipped): {result['skipped_already_applied']}")

        elif command == "status":
            from migrations.run_all_migrations import MigrationRunner
            for name, is_applied in MigrationRunner().status().items():
                print(f"{'[applied]' if is_applied else '[pending]'} {name}")

        else:
            logger.error(f"Unknown command: {command}")
            print("Available commands: upgrade, downgrade, sample, migrate_contracts, rollback, migrate_all, status")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()