import unittest

from slice_op import program_slice_backwards


class FakeNode(dict):
    def __init__(self, pdg, index, **attrs):
        super(FakeNode, self).__init__(**attrs)
        self._pdg = pdg
        self._index = index
        self._predecessors = []
        self._successors = []

    def predecessors(self):
        return [self._pdg.vs[i] for i in self._predecessors]

    def successors(self):
        return [self._pdg.vs[i] for i in self._successors]


class FakePDG(object):
    def __init__(self):
        self.vs = []

    def add_node(self, **attrs):
        node = FakeNode(self, len(self.vs), **attrs)
        self.vs.append(node)
        return node

    def add_edge(self, src, dst):
        src._successors.append(dst._index)
        dst._predecessors.append(src._index)


class SliceComponentTest(unittest.TestCase):
    def test_backward_slice_excludes_unused_variables(self):
        pdg = FakePDG()
        func = pdg.add_node(
            name="func_1",
            type="Function",
            location="1:1",
            functionId="func_1",
            code="int demo() {",
        )
        assign_a = pdg.add_node(
            name="assign_a_node",
            type="IdentifierDeclStatement",
            location="2:1",
            functionId="func_1",
            code="int a = 1;",
        )
        assign_b = pdg.add_node(
            name="assign_b_node",
            type="IdentifierDeclStatement",
            location="3:1",
            functionId="func_1",
            code="int b = 2;",
        )
        pdg.add_node(
            name="unused_var_node",
            type="IdentifierDeclStatement",
            location="4:1",
            functionId="func_1",
            code="int unused = 99;",
        )
        critical = pdg.add_node(
            name="critical_node",
            type="ExpressionStatement",
            location="5:1",
            functionId="func_1",
            code="int sum = a + b;",
        )

        pdg.add_edge(assign_a, critical)
        pdg.add_edge(assign_b, critical)

        sliced_nodes = program_slice_backwards(pdg, [critical])
        sliced_codes = [node["code"] for node in sliced_nodes]

        self.assertEqual(
            sliced_codes,
            [
                "int demo() {",
                "int a = 1;",
                "int b = 2;",
                "int sum = a + b;",
            ],
        )

    def test_backward_slice_with_no_dependencies_keeps_critical_line(self):
        pdg = FakePDG()
        pdg.add_node(
            name="func_2",
            type="Function",
            location="1:1",
            functionId="func_2",
            code="int demo2() {",
        )
        critical = pdg.add_node(
            name="critical_no_dep_node",
            type="ExpressionStatement",
            location="2:1",
            functionId="func_2",
            code="dangerous_call();",
        )

        sliced_nodes = program_slice_backwards(pdg, [critical])
        sliced_codes = [node["code"] for node in sliced_nodes]

        self.assertEqual(sliced_codes, ["int demo2() {", "dangerous_call();"])


if __name__ == "__main__":
    unittest.main()
