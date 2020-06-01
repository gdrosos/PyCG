import ast
import os
import importlib

from pycg.machinery.definitions import DefinitionManager, Definition
from pycg import utils
from pycg.processing.base import ProcessingBase

class PreProcessor(ProcessingBase):
    def __init__(self, filename, modname,
            import_manager, scope_manager, def_manager, class_manager,
            module_manager, modules_analyzed=None):
        super().__init__(filename, modname, modules_analyzed)

        self.modname = modname
        self.mod_dir = "/".join(self.filename.split("/")[:-1])

        self.import_manager = import_manager
        self.scope_manager = scope_manager
        self.def_manager = def_manager
        self.class_manager = class_manager
        self.module_manager = module_manager

    def _get_fun_defaults(self, node):
        defaults = {}
        start = len(node.args.args) - len(node.args.defaults)
        for cnt, d in enumerate(node.args.defaults, start=start):
            if not d:
                continue

            self.visit(d)
            defaults[node.args.args[cnt].arg] = self.decode_node(d)

        start = len(node.args.kwonlyargs) - len(node.args.kw_defaults)
        for cnt, d in enumerate(node.args.kw_defaults, start=start):
            if not d:
                continue
            self.visit(d)
            defaults[node.args.kwonlyargs[cnt].arg] = self.decode_node(d)

        return defaults

    def analyze_submodule(self, modname):
        super().analyze_submodule(PreProcessor, modname,
            self.import_manager, self.scope_manager, self.def_manager, self.class_manager,
            self.module_manager, modules_analyzed=self.get_modules_analyzed())

    def visit_Module(self, node):
        def iterate_mod_items(items, const):
            for item in items:
                defi = self.def_manager.get(item)
                if not defi:
                    defi = self.def_manager.create(item, const)

                splitted = item.split(".")
                name = splitted[-1]
                parentns = ".".join(splitted[:-1])
                self.scope_manager.get_scope(parentns).add_def(name, defi)

        self.import_manager.set_current_mod(self.modname, self.filename)

        self.module_manager.create(self.modname, self.filename)

        # initialize module scopes
        items = self.scope_manager.handle_module(self.modname,
            self.filename, self.contents)

        root_sc = self.scope_manager.get_scope(self.modname)
        root_defi = self.def_manager.get(self.modname)
        if not root_defi:
            root_defi = self.def_manager.create(self.modname, utils.constants.MOD_DEF)
        root_sc.add_def(self.modname.split(".")[-1], root_defi)

        # create function and class defs and add them to their scope
        # we do this here, because scope_manager doesn't have an
        # interface with def_manager, and we want function definitions
        # to have the correct points_to set
        iterate_mod_items(items["functions"], utils.constants.FUN_DEF)
        iterate_mod_items(items["classes"], utils.constants.CLS_DEF)

        defi = self.def_manager.get(self.modname)
        if not defi:
            defi = self.def_manager.create(self.modname, utils.constants.MOD_DEF)

        super().visit_Module(node)

    def visit_Import(self, node, prefix='', level=0):
        """
        For imports of the form
            `from something import anything`
        prefix is set to "something".
        For imports of the form
            `from .relative import anything`
        level is set to a number indicating the number
        of parent directories (e.g. in this case level=1)
        """
        def handle_src_name(name):
            # Get the module name and prepend prefix if necessary
            src_name = name
            if prefix:
                src_name = prefix + "." + src_name
            return src_name

        def handle_scopes(tgt_name, modname):
            def create_def(scope, name, imported_def):
                if not name in scope.get_defs():
                    def_ns = utils.join_ns(scope.get_ns(), name)
                    defi = self.def_manager.get(def_ns)
                    if not defi:
                        defi = self.def_manager.assign(def_ns, imported_def)
                    defi.get_name_pointer().add(imported_def.get_ns())
                    current_scope.add_def(name, defi)

            current_scope = self.scope_manager.get_scope(self.current_ns)
            imported_scope = self.scope_manager.get_scope(modname)
            if tgt_name == "*":
                for name, defi in imported_scope.get_defs().items():
                    create_def(current_scope, name, defi)
                    current_scope.get_def(name).get_name_pointer().add(defi.get_ns())
            else:
                # if it exists in the imported scope then copy it
                defi = imported_scope.get_def(tgt_name)
                if defi:
                    create_def(current_scope, tgt_name, defi)
                    current_scope.get_def(tgt_name).get_name_pointer().add(imported_scope.get_def(tgt_name).get_ns())

        def add_external_def(name, target):
            # add an external def for the name
            defi = self.def_manager.get(name)
            if not defi:
                defi = self.def_manager.create(name, utils.constants.EXT_DEF)
            scope = self.scope_manager.get_scope(self.current_ns)
            if target != "*":
                # add a def for the target that points to the name
                tgt_ns = utils.join_ns(scope.get_ns(), target)
                tgt_defi = self.def_manager.get(tgt_ns)
                if not tgt_defi:
                    tgt_defi = self.def_manager.create(tgt_ns, utils.constants.EXT_DEF)
                tgt_defi.get_name_pointer().add(defi.get_ns())
                scope.add_def(target, tgt_defi)

        for import_item in node.names:
            src_name = handle_src_name(import_item.name)
            tgt_name = import_item.asname if import_item.asname else import_item.name
            imported_name = self.import_manager.handle_import(src_name, level)

            if not imported_name:
                add_external_def(src_name, tgt_name)
                continue

            fname = self.import_manager.get_filepath(imported_name)
            if not fname:
                add_external_def(src_name, tgt_name)
                continue
            # only analyze modules under the current directory
            if self.import_manager.get_mod_dir() in fname:
                if not imported_name in self.modules_analyzed:
                    self.analyze_submodule(imported_name)
                handle_scopes(tgt_name, imported_name)
            else:
                add_external_def(src_name, tgt_name)

        # handle all modules that were not analyzed
        for modname in self.import_manager.get_imports(self.modname):
            fname = self.import_manager.get_filepath(modname)

            if not fname:
                continue
            # only analyze modules under the current directory
            if self.import_manager.get_mod_dir() in fname and \
                not modname in self.modules_analyzed:
                    self.analyze_submodule(modname)


    def visit_ImportFrom(self, node):
        self.visit_Import(node, prefix=node.module, level=node.level)

    def _handle_function_def(self, node, fn_name):
        current_def = self.def_manager.get(self.current_ns)

        defaults = self._get_fun_defaults(node)

        fn_def = self.def_manager.handle_function_def(self.current_ns, fn_name)

        mod = self.module_manager.get(self.modname)
        if not mod:
            mod = self.module_manager.create(self.modname, self.filename)
        mod.add_method(fn_def.get_ns())

        defs_to_create = []
        name_pointer = fn_def.get_name_pointer()

        # TODO: static methods can be created using the staticmethod() function too
        is_static_method = False
        if hasattr(node, "decorator_list"):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == utils.constants.STATIC_METHOD:
                    is_static_method = True

        if current_def.get_type() == utils.constants.CLS_DEF and not is_static_method and node.args.args:
            arg_ns = utils.join_ns(fn_def.get_ns(), node.args.args[0].arg)
            arg_def = self.def_manager.get(arg_ns)
            if not arg_def:
                arg_def = self.def_manager.create(arg_ns, utils.constants.NAME_DEF)
            arg_def.get_name_pointer().add(current_def.get_ns())

            self.scope_manager.handle_assign(fn_def.get_ns(), arg_def.get_name(), arg_def)
            node.args.args = node.args.args[1:]

        for pos, arg in enumerate(node.args.args):
            arg_ns = utils.join_ns(fn_def.get_ns(), arg.arg)
            name_pointer.add_pos_arg(pos, arg.arg, arg_ns)
            defs_to_create.append(arg_ns)

        for arg in node.args.kwonlyargs:
            arg_ns = utils.join_ns(fn_def.get_ns(), arg.arg)
            # TODO: add_name_arg function
            name_pointer.add_name_arg(arg.arg, arg_ns)
            defs_to_create.append(arg_ns)

        # TODO: Add support for kwargs and varargs
        #if node.args.kwarg:
        #    pass
        #if node.args.vararg:
        #    pass

        for arg_ns in defs_to_create:
            arg_def = self.def_manager.get(arg_ns)
            if not arg_def:
                arg_def = self.def_manager.create(arg_ns, utils.constants.NAME_DEF)

            self.scope_manager.handle_assign(fn_def.get_ns(), arg_def.get_name(), arg_def)

            # has a default
            arg_name = arg_ns.split(".")[-1]
            if defaults.get(arg_name, None):
                for default in defaults[arg_name]:
                    if isinstance(default, Definition):
                        arg_def.get_name_pointer().add(default.get_ns())
                        if default.is_function_def():
                            arg_def.get_name_pointer().add(default.get_ns())
                        else:
                            arg_def.merge(default)
                    else:
                        arg_def.get_lit_pointer().add(default)
        return fn_def

    def visit_FunctionDef(self, node):
        fn_def = self._handle_function_def(node, node.name)

        super().visit_FunctionDef(node)

    def visit_Tuple(self, node):
        # node.ctx == ast.Load means get
        # node.ctx == ast.Store means set
        for elt in node.elts:
            self.visit(elt)

    def visit_Assign(self, node):
        self._visit_assign(node)

    def visit_Return(self, node):
        self._visit_return(node)

    def visit_Call(self, node):
        self.visit(node.func)
        # if it is not a name there's nothing we can do here
        # ModuleVisitor will be able to resolve those calls
        # since it'll have the name tracking information
        if not isinstance(node.func, ast.Name):
            return

        fullns = utils.join_ns(self.current_ns, node.func.id)

        defi = self.scope_manager.get_def(self.current_ns, node.func.id)
        if not defi:
            return

        if defi.get_type() == utils.constants.CLS_DEF:
            defi = self.def_manager.get(utils.join_ns(defi.get_ns(), utils.constants.CLS_INIT))
            if not defi:
                return

        self.iterate_call_args(defi, node)

    def visit_Lambda(self, node):
        # The name of a lambda is defined by the counter of the current scope
        current_scope = self.scope_manager.get_scope(self.current_ns)
        lambda_counter = current_scope.inc_lambda_counter()
        lambda_name = utils.get_lambda_name(lambda_counter)
        lambda_full_ns = utils.join_ns(self.current_ns, lambda_name)

        # create a scope for the lambda
        self.scope_manager.create_scope(lambda_full_ns, current_scope)
        lambda_def = self._handle_function_def(node, lambda_name)
        # add it to the current scope
        current_scope.add_def(lambda_name, lambda_def)

        super().visit_Lambda(node, lambda_name)

    def visit_Dict(self, node):
        # 1. create a scope using a counter
        # 2. Iterate keys and add them as children of the scope
        # 3. Iterate values and makes a points to connection with the keys
        current_scope = self.scope_manager.get_scope(self.current_ns)
        dict_counter = current_scope.inc_dict_counter()
        dict_name = utils.get_dict_name(dict_counter)
        dict_full_ns = utils.join_ns(self.current_ns, dict_name)

        # create a scope for the lambda
        dict_scope = self.scope_manager.create_scope(dict_full_ns, current_scope)

        # Create a dict definition
        dict_def = self.def_manager.get(dict_full_ns)
        if not dict_def:
            dict_def = self.def_manager.create(dict_full_ns, utils.constants.NAME_DEF)
        # add it to the current scope
        current_scope.add_def(dict_name, dict_def)

        self.name_stack.append(dict_name)
        for key, value in zip(node.keys, node.values):
            self.visit(key)
            self.visit(value)
            decoded_key = self.decode_node(key)
            decoded_value = self.decode_node(value)

            # iterate decoded keys and values
            # to do the assignment operation
            for k in decoded_key:
                if isinstance(k, Definition):
                    # get literal pointer
                    names = k.get_lit_pointer().get()
                else:
                    names = set()
                    names.add(k)
                for name in names:
                    # create a definition for the key
                    # TODO: convertion of int to str will result in false positives
                    key_full_ns = utils.join_ns(dict_def.get_ns(), str(name))
                    key_def = self.def_manager.get(key_full_ns)
                    if not key_def:
                        key_def = self.def_manager.create(key_full_ns, utils.constants.NAME_DEF)
                    dict_scope.add_def(str(name), key_def)
                    for v in decoded_value:
                        if isinstance(v, Definition):
                            key_def.get_name_pointer().add(v.get_ns())
                        else:
                            key_def.get_lit_pointer().add(v)
        self.name_stack.pop()


    def visit_ClassDef(self, node):
        # create a definition for the class (node.name)
        cls_def = self.def_manager.handle_class_def(self.current_ns, node.name)

        mod = self.module_manager.get(self.modname)
        if not mod:
            mod = self.module_manager.create(self.modname, self.filename)
        mod.add_method(cls_def.get_ns())

        # iterate bases to compute MRO for the class
        cls = self.class_manager.get(cls_def.get_ns())
        if not cls:
            cls = self.class_manager.create(cls_def.get_ns(), self.modname)
        for base in node.bases:
            # all bases are of the type ast.Name
            self.visit(base)

            bases = self.decode_node(base)
            for base_def in bases:
                if not isinstance(base_def, Definition):
                    continue
                names = set()
                if base_def.get_name_pointer().get():
                    names = base_def.get_name_pointer().get()
                else:
                    names.add(base_def.get_ns())
                for name in names:
                    # add the base as a parent
                    cls.add_parent(name)

                    # add the base's parents
                    parent_cls = self.class_manager.get(name)
                    if parent_cls:
                        cls.add_parent(parent_cls.get_mro())

        cls.compute_mro()

        super().visit_ClassDef(node)

    def analyze(self):
        if not self.import_manager.get_node(self.modname):
            self.import_manager.create_node(self.modname)
            self.import_manager.set_filepath(self.modname, self.filename)

        self.visit(ast.parse(self.contents, self.filename))
