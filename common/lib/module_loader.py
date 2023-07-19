"""
Load modules and datasources dynamically
"""
from pathlib import Path
import importlib
import inspect
import pickle
import sys
import re

from common.config_manager import config


class ModuleCollector:
    """
    Collects all modular appendages of 4CAT

    On init, an object of this class collects all datasources and workers that
    have been added to this 4CAT installation. The metadata of these is then
    stored for later access.

    Datasources are found in the "datasources" folder in root. Workers are
    found in datasource folders or the default "processors" and
    "backend/workers" folder. All these folders are scanned for both
    processors and workers (processors being a specific kind of worker).
    """
    ignore = []
    missing_modules = {}
    log_buffer = None

    PROCESSOR = 1
    WORKER = 2

    workers = {}
    processors = {}
    datasources = {}

    def __init__(self):
        """
        Load data sources and workers

        Datasources are loaded first so that the datasource folders may be
        scanned for workers subsequently.
        """
        # this can be flushed later once the logger is available
        self.log_buffer = ""

        self.load_datasources()
        self.load_modules()

        # now we know all workers, we can add some extra metadata to the
        # datasources, e.g. whether they have an associated search worker
        self.expand_datasources()

        # cache module-defined config options for use by the config manager
        module_config = {}
        for worker in self.workers.values():
            if hasattr(worker, "config") and type(worker.config) is dict:
                module_config.update(worker.config)

        with config.get("PATH_ROOT").joinpath("config/module_config.bin").open("wb") as outfile:
            pickle.dump(module_config, outfile)

        # load from cache
        config.load_user_settings()

    @staticmethod
    def is_4cat_class(object, only_processors=False):
        """
        Determine if a module member is a worker class we can use
        """
        # it would be super cool to just use issubclass() here!
        # but that requires importing the classes themselves, which leads to
        # circular imports
        # todo: fix this because this sucks
        # agreed - Dale
        parent_classes = {"BasicWorker", "BasicProcessor", "Search", "SearchWithScope", "Search4Chan",
                          "ProcessorPreset", "TwitterStatsBase", "BaseFilter", "TwitterAggregatedStats", "ColumnFilter",
                          "BasicJSONScraper", "BoardScraper4chan", "ThreadScraper4chan"}
        if only_processors:
            # only allow processors
            for worker_only_class in ["BasicWorker", "BasicJSONScraper", "BoardScraper4chan", "ThreadScraper4chan"]:
                parent_classes.remove(worker_only_class)

        return inspect.isclass(object) and \
               parent_classes & set([f.__name__ for f in object.__bases__]) and \
               object.__name__ not in("BasicProcessor", "BasicWorker") and \
               not inspect.isabstract(object)

    def load_modules(self):
        """
        Load modules

        Modules are workers and (as a subset of workers) postprocessors. These
        are found by importing any python files found in the given locations,
        and looking for relevant classes within those python files, that extend
        `BasicProcessor` or `BasicWorker` and are not abstract.
        """
        # look for workers and processors in pre-defined folders and datasources

        paths = [Path(config.get('PATH_ROOT'), "processors"), Path(config.get('PATH_ROOT'), "backend", "workers"),
                 *[self.datasources[datasource]["path"] for datasource in self.datasources]]

        root_match = re.compile(r"^%s" % re.escape(str(config.get('PATH_ROOT'))))
        root_path = Path(config.get('PATH_ROOT'))

        for folder in paths:
            # loop through folders, and files in those folders, recursively
            for file in folder.rglob("*.py"):
                # determine module name for file
                # reduce path to be relative to 4CAT root
                module_name = ".".join(file.parts[len(root_path.parts):-1] + (file.stem,))

                # check if we've already loaded this module
                if module_name in self.ignore:
                    continue

                if module_name in sys.modules:
                    # This skips processors/datasources that were loaded by others and may not yet be captured
                    pass

                # try importing
                try:
                    module = importlib.import_module(module_name)
                except (SyntaxError, ImportError) as e:
                    # this is fine, just ignore this data source and give a heads up
                    self.ignore.append(module_name)
                    key_name = e.name if hasattr(e, "name") else module_name
                    if key_name not in self.missing_modules:
                        self.missing_modules[key_name] = [module_name]
                    else:
                        self.missing_modules[key_name].append(module_name)
                    continue

                # see if module contains the right type of content by looping
                # through all of its members
                components = inspect.getmembers(module, predicate=self.is_4cat_class)
                for component in components:
                    if component[1].type in self.workers:
                        # already indexed
                        continue

                    # extract data that is useful for the scheduler and other
                    # parts of 4CAT
                    relative_path = root_match.sub("", str(file))

                    self.workers[component[1].type] = component[1]
                    self.workers[component[1].type].filepath = relative_path

                    # we can't use issubclass() because for that we would need
                    # to import BasicProcessor, which would lead to a circular
                    # import
                    if self.is_4cat_class(component[1], only_processors=True):
                        self.processors[component[1].type] = self.workers[component[1].type]

        # sort by category for more convenient display in interfaces
        sorted_processors = {id: self.processors[id] for id in
                             sorted(self.processors)}
        categorised_processors = {id: sorted_processors[id] for id in
                                  sorted(sorted_processors,
                                         key=lambda item: "0" if sorted_processors[item].category == "Presets" else
                                         sorted_processors[item].category)}

        # Give a heads-up if not all modules were installed properly
        if self.missing_modules:
            warning = "Warning: Not all modules could be found, which might cause data sources and modules to not " \
                      "function.\nMissing modules:\n"
            for missing_module, processor_list in self.missing_modules.items():
                warning += "\t%s (for %s)\n" % (missing_module, ", ".join(processor_list))

            self.log_buffer = warning


        self.processors = categorised_processors

    def load_datasources(self):
        """
        Load datasources

        This looks for folders within the datasource root folder that contain
        an `__init__.py` defining an `init_datasource` function and a
        `DATASOURCE` constant. The latter is taken as the ID for this
        datasource.
        """
        for subdirectory in Path(config.get('PATH_ROOT'), "datasources").iterdir():
            # folder name, also the name used in config.py
            folder_name = subdirectory.parts[-1]

            # determine module name
            module_name = "datasources." + folder_name
            try:
                datasource = importlib.import_module(module_name)
            except ImportError as e:
                continue

            if not hasattr(datasource, "init_datasource") or not hasattr(datasource, "DATASOURCE"):
                continue

            datasource_id = datasource.DATASOURCE

            self.datasources[datasource_id] = {
                "expire-datasets": config.get("datasources.expiration", {}).get(datasource_id, None),
                "path": subdirectory,
                "name": datasource.NAME if hasattr(datasource, "NAME") else datasource_id,
                "id": subdirectory.parts[-1],
                "init": datasource.init_datasource,
                "config": {} if not hasattr(datasource, "config") else datasource.config
            }

        sorted_datasources = {datasource_id: self.datasources[datasource_id] for datasource_id in
                              sorted(self.datasources, key=lambda id: self.datasources[id]["name"])}
        self.datasources = sorted_datasources

    def expand_datasources(self):
        """
        Expand datasource metadata

        Some datasource metadata can only be known after all workers have been
        loaded, e.g. whether there is a search worker for the datasource. This
        function takes care of populating those values.
        """
        for datasource_id in self.datasources:
            worker = self.workers.get("%s-search" % datasource_id)
            self.datasources[datasource_id]["has_worker"] = bool(worker)
            self.datasources[datasource_id]["has_options"] = self.datasources[datasource_id]["has_worker"] and \
                                                             bool(self.workers["%s-search" % datasource_id].get_options())
            self.datasources[datasource_id]["importable"] = worker and hasattr(worker, "is_from_extension") and worker.is_from_extension

    def load_worker_class(self, worker):
        """
        Get class for worker

        This import worker modules on-demand, so the code is only loaded if a
        worker that needs the code is actually queued and run

        :return:  Worker class for the given worker metadata
        """
        module = worker["module"]
        if module not in sys.modules:
            importlib.import_module(module)

        return getattr(sys.modules[module], worker["class_name"])
