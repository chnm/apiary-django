"""
Simple test management command for demonstration purposes.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'A simple test command that logs a message'

    def add_arguments(self, parser):
        parser.add_argument(
            '--message',
            type=str,
            default='Hello from test command!',
            help='Custom message to log',
        )

    def handle(self, *args, **options):
        message = options['message']
        timestamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.stdout.write(
            self.style.SUCCESS(f'[{timestamp}] {message}')
        )
        
        return f'Command executed successfully at {timestamp}'
