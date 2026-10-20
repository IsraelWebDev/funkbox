from funkgl.context import gl_info


def test_context(gl):
    assert gl is not None
    info = gl_info()
    assert info["GL_RENDERER"].strip()
    assert info["GL_VERSION"].strip()
