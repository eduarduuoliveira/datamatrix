import cv2
import numpy as np
import json
import sqlite3
import socket
import threading
import time
import csv
from datetime import datetime
from pylibdmtx.pylibdmtx import decode
from PyQt5 import QtWidgets, QtGui, QtCore

CONFIG_PATH = 'config.json'
DB_PATH = 'codigos.db'

# Load config
def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=4)

# DB setup
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS codigos
                (codigo TEXT, data_hora TEXT)''')
conn.commit()

def salvar_codigo(codigo):
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("INSERT INTO codigos VALUES (?, ?)", (codigo, agora))
    conn.commit()

def exportar_db_para_csv():
    with open('codigos_exportados.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Código', 'Data e Hora'])
        for row in cursor.execute("SELECT * FROM codigos"):
            writer.writerow(row)

# Server to send code
def start_server():
    cfg = load_config()
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((cfg['server_ip'], cfg['server_port']))
    server_socket.listen(1)
    print("Servidor aguardando conexões...")
    client_socket, addr = server_socket.accept()
    print(f"Conectado a {addr}")
    return client_socket

class CameraThread(QtCore.QThread):
    codigo_detectado = QtCore.pyqtSignal(str, float, QtGui.QImage)

    def __init__(self, filters):
        super().__init__()
        self.running = True
        self.cfg = load_config()
        self.filters = filters
        self.cap = cv2.VideoCapture(self.cfg['camera_index'])
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg['res_width'])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg['res_height'])
        self.last_time = 0

    def apply_filters(self, frame):
        if self.filters['gray']:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        frame = cv2.convertScaleAbs(frame, alpha=self.filters['contrast'], beta=self.filters['brightness'])
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hsv[..., 1] = np.clip(hsv[..., 1] * self.filters['saturation'], 0, 255)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        return frame

    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                continue

            frame = self.apply_filters(frame)

            # Convert to RGB for pylibdmtx
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            decoded = decode(frame_rgb)

            if decoded and time.time() - self.last_time > 5:
                for obj in decoded:
                    codigo = obj.data.decode('utf-8')

                    # A posição e tamanho do retângulo
                    x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                    salvar_codigo(codigo)
                    self.last_time = time.time()

                    rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h_img, w_img, ch = rgb_image.shape
                    qt_image = QtGui.QImage(rgb_image.data, w_img, h_img, ch * w_img, QtGui.QImage.Format_RGB888)

                    # pylibdmtx não retorna confiança, vamos fixar 100%
                    self.codigo_detectado.emit(codigo, 100.0, qt_image)

                    self.enviar_para_cliente(codigo)
                    break
            else:
                # Atualiza a imagem mesmo sem detectar para não travar tela
                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h_img, w_img, ch = rgb_image.shape
                qt_image = QtGui.QImage(rgb_image.data, w_img, h_img, ch * w_img, QtGui.QImage.Format_RGB888)
                self.codigo_detectado.emit("", 0.0, qt_image)

    def enviar_para_cliente(self, codigo):
        try:
            cfg = load_config()
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((cfg['client_ip'], cfg['client_port']))
            client.sendall(codigo.encode('utf-8'))
            client.close()
        except Exception as e:
            print(f"Erro ao enviar código ao cliente: {e}")

    def stop(self):
        self.running = False
        self.cap.release()

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Leitor DataMatrix")
        self.setGeometry(100, 100, 1000, 600)

        self.filters = {
            'gray': False,
            'brightness': 0,
            'contrast': 1.0,
            'saturation': 1.0
        }

        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)

        # Aba Câmera
        self.camera_tab = QtWidgets.QWidget()
        self.camera_layout = QtWidgets.QVBoxLayout(self.camera_tab)
        self.image_label = QtWidgets.QLabel("Imagem da câmera")
        self.result_label = QtWidgets.QLabel("Código: N/A | Confiança: N/A")
        self.export_btn = QtWidgets.QPushButton("Exportar DB (CSV)")
        self.export_btn.clicked.connect(self.exportar_dados)
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Código", "Data e Hora"])
        self.atualizar_tabela()

        self.camera_layout.addWidget(self.image_label)
        self.camera_layout.addWidget(self.result_label)
        self.camera_layout.addWidget(self.export_btn)
        self.camera_layout.addWidget(self.table)
        self.tabs.addTab(self.camera_tab, "Câmera")

        # Aba Configuração
        self.config_tab = QtWidgets.QWidget()
        self.config_layout = QtWidgets.QFormLayout(self.config_tab)
        self.user_input = QtWidgets.QLineEdit()
        self.pass_input = QtWidgets.QLineEdit()
        self.pass_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.cam_index_input = QtWidgets.QSpinBox()
        self.cam_index_input.setRange(0, 10)
        self.res_x_input = QtWidgets.QSpinBox()
        self.res_x_input.setRange(100, 3840)
        self.res_y_input = QtWidgets.QSpinBox()
        self.res_y_input.setRange(100, 2160)
        self.ip_input = QtWidgets.QLineEdit()
        self.port_input = QtWidgets.QSpinBox()
        self.port_input.setRange(1, 65535)
        self.save_btn = QtWidgets.QPushButton("Salvar")

        self.config_layout.addRow("Usuário:", self.user_input)
        self.config_layout.addRow("Senha:", self.pass_input)
        self.config_layout.addRow("Índice Câmera:", self.cam_index_input)
        self.config_layout.addRow("Largura:", self.res_x_input)
        self.config_layout.addRow("Altura:", self.res_y_input)
        self.config_layout.addRow("IP Cliente:", self.ip_input)
        self.config_layout.addRow("Porta Cliente:", self.port_input)
        self.config_layout.addRow(self.save_btn)
        self.tabs.addTab(self.config_tab, "Configurações")

        # Aba Filtros
        self.filter_tab = QtWidgets.QWidget()
        self.filter_layout = QtWidgets.QFormLayout(self.filter_tab)
        self.gray_cb = QtWidgets.QCheckBox("Tons de cinza")
        self.brightness_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.brightness_slider.setRange(-100, 100)
        self.contrast_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.contrast_slider.setRange(10, 300)
        self.saturation_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.saturation_slider.setRange(10, 300)

        self.filter_layout.addRow(self.gray_cb)
        self.filter_layout.addRow("Brilho:", self.brightness_slider)
        self.filter_layout.addRow("Contraste:", self.contrast_slider)
        self.filter_layout.addRow("Saturação:", self.saturation_slider)
        self.tabs.addTab(self.filter_tab, "Filtros")

        # Conexões
        self.save_btn.clicked.connect(self.salvar_config)
        self.gray_cb.stateChanged.connect(self.atualizar_filtros)
        self.brightness_slider.valueChanged.connect(self.atualizar_filtros)
        self.contrast_slider.valueChanged.connect(self.atualizar_filtros)
        self.saturation_slider.valueChanged.connect(self.atualizar_filtros)

        self.cam_thread = CameraThread(self.filters)
        self.cam_thread.codigo_detectado.connect(self.atualizar_interface)
        self.cam_thread.start()

    def atualizar_filtros(self):
        self.filters['gray'] = self.gray_cb.isChecked()
        self.filters['brightness'] = self.brightness_slider.value()
        self.filters['contrast'] = self.contrast_slider.value() / 100.0
        self.filters['saturation'] = self.saturation_slider.value() / 100.0

    def atualizar_interface(self, codigo, certeza, qt_image):
        if codigo:
            self.result_label.setText(f"Código: {codigo} | Confiança: {certeza:.2f}%")
        else:
            self.result_label.setText("Código: N/A | Confiança: N/A")

        self.image_label.setPixmap(QtGui.QPixmap.fromImage(qt_image))
        self.atualizar_tabela()

    def atualizar_tabela(self):
        cursor.execute("SELECT * FROM codigos ORDER BY data_hora DESC LIMIT 100")
        rows = cursor.fetchall()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(row[0]))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(row[1]))

    def exportar_dados(self):
        exportar_db_para_csv()
        QtWidgets.QMessageBox.information(self, "Exportado", "Dados exportados para 'codigos_exportados.csv'.")

    def salvar_config(self):
        if self.user_input.text() == 'admin' and self.pass_input.text() == 'admin':
            cfg = {
                "camera_index": self.cam_index_input.value(),
                "res_width": self.res_x_input.value(),
                "res_height": self.res_y_input.value(),
                "client_ip": self.ip_input.text(),
                "client_port": self.port_input.value(),
                "server_ip": "0.0.0.0",
                "server_port": 9999
            }
            save_config(cfg)
            QtWidgets.QMessageBox.information(self, "Salvo", "Configuração salva com sucesso.")
        else:
            QtWidgets.QMessageBox.critical(self, "Erro", "Usuário ou senha incorretos.")

    def closeEvent(self, event):
        self.cam_thread.stop()
        event.accept()

if __name__ == '__main__':
    import sys
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
