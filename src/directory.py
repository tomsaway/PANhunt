import os

from job import Job


class Directory:

    path: str

    def __init__(self, path: str) -> None:
        self.path = path

    def get_children(self) -> list[Job]:
        jobs: list[Job] = []
        for root, _, files in os.walk(self.path):
            for file in files:
                jobs.append(Job(
                    basename=file, dirname=root))

        return jobs
