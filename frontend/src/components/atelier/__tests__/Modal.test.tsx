import { render, screen, fireEvent } from '@testing-library/react'
import Modal from '../Modal'

beforeEach(() => {
  document.body.innerHTML = ''
})

it('renders nothing when closed', () => {
  const { container } = render(
    <Modal open={false} onClose={() => {}} title="Test">
      <p>content</p>
    </Modal>,
  )
  expect(container.querySelector('.atelier-modal-root')).toBeNull()
})

it('renders modal with title and content when open', () => {
  render(
    <Modal open={true} onClose={() => {}} title="My Modal">
      <p>modal body</p>
    </Modal>,
  )
  expect(screen.getByText('My Modal')).toBeTruthy()
  expect(screen.getByText('modal body')).toBeTruthy()
})

it('calls onClose when Escape is pressed', () => {
  const onClose = vi.fn()
  render(
    <Modal open={true} onClose={onClose} title="Escape Test">
      <p>body</p>
    </Modal>,
  )
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('calls onClose when backdrop is clicked', () => {
  const onClose = vi.fn()
  render(
    <Modal open={true} onClose={onClose} title="Backdrop Test">
      <p>body</p>
    </Modal>,
  )
  const backdrop = document.querySelector('.atelier-modal-backdrop')!
  fireEvent.click(backdrop)
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('does not call onClose when modal content is clicked', () => {
  const onClose = vi.fn()
  render(
    <Modal open={true} onClose={onClose} title="Click Inside">
      <p>body</p>
    </Modal>,
  )
  const panel = document.querySelector('.atelier-modal-panel')!
  fireEvent.click(panel)
  expect(onClose).not.toHaveBeenCalled()
})

it('renders custom footer instead of default', () => {
  render(
    <Modal open={true} onClose={() => {}} title="Custom" footer={<button>custom</button>}>
      <p>body</p>
    </Modal>,
  )
  expect(screen.getByText('custom')).toBeTruthy()
  expect(screen.queryByText('确定')).toBeNull()
})

it('renders default footer with okText and cancelText', () => {
  render(
    <Modal open={true} onClose={() => {}} onOk={() => {}} okText="确认" cancelText="取消">
      <p>body</p>
    </Modal>,
  )
  expect(screen.getByText('确认')).toBeTruthy()
  expect(screen.getByText('取消')).toBeTruthy()
})

it('shows loading state on ok button', () => {
  render(
    <Modal open={true} onClose={() => {}} onOk={() => {}} confirmLoading={true} okText="保存">
      <p>body</p>
    </Modal>,
  )
  const ok = screen.getByText('保存')
  expect(ok.closest('button')).toHaveAttribute('aria-busy')
})

it('calls onOk when ok button clicked', () => {
  const onOk = vi.fn()
  render(
    <Modal open={true} onClose={() => {}} onOk={onOk} okText="保存">
      <p>body</p>
    </Modal>,
  )
  fireEvent.click(screen.getByText('保存'))
  expect(onOk).toHaveBeenCalledTimes(1)
})

it('renders close button and calls onClose', () => {
  const onClose = vi.fn()
  render(
    <Modal open={true} onClose={onClose} title="Closable">
      <p>body</p>
    </Modal>,
  )
  const closeBtn = document.querySelector('.atelier-modal-close')!
  fireEvent.click(closeBtn)
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('sets aria-modal and role attributes', () => {
  render(
    <Modal open={true} onClose={() => {}} title="A11y">
      <p>body</p>
    </Modal>,
  )
  const panel = document.querySelector('.atelier-modal-panel')!
  expect(panel.getAttribute('role')).toBe('dialog')
  expect(panel.getAttribute('aria-modal')).toBe('true')
})
