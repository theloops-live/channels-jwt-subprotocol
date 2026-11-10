from django.core.management import call_command

from tests.settings import configure

configure()
call_command("migrate", verbosity=0, run_syncdb=True)
