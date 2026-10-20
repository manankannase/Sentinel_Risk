"""Create a checked PostgreSQL custom archive using a private libpq service configuration."""
import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix(args.output.suffix+'.sha256').exists():
        parser.error('Backup target must be new')
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=args.output.parent, prefix='.backup-')
    os.close(fd)
    temporary = Path(name)
    env = {**os.environ, 'PGSERVICE': args.service}
    try:
        subprocess.run(['pg_dump','--format=custom','--no-owner','--no-privileges','--file',str(temporary)],
                       env=env,check=True)
        subprocess.run(['pg_restore','--list',str(temporary)], env=env,check=True,stdout=subprocess.DEVNULL)
        with temporary.open('rb') as handle:
            digest = hashlib.file_digest(handle,'sha256').hexdigest()
        # Exclusive linking refuses a target created by a concurrent backup job.
        os.link(temporary,args.output)
        with args.output.with_suffix(args.output.suffix+'.sha256').open('x') as handle:
            handle.write(f'{digest}  {args.output.name}\n')
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Archive and checksum created: {args.output}. Verify an isolated restore before relying on this backup.')


if __name__=='__main__':
    main()
